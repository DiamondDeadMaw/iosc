from collections.abc import Sequence
import hashlib
import struct
from typing import Any

from iosc.core.errors import MachOError, SigningError
from iosc.formats import asn1, macho, pki
from iosc.formats.pki import SigningIdentity

# re-bound from iosc.formats.macho
# signer and the tests reach for these through codesign
FAT_MAGICS = macho.FAT_MAGICS
MH_MAGIC_64 = macho.MH_MAGIC_64
MH_EXECUTE = macho.MH_EXECUTE

LC_SEGMENT_64 = macho.LC_SEGMENT_64
LC_CODE_SIGNATURE = macho.LC_CODE_SIGNATURE
LINKEDIT_DATA_COMMAND_SIZE = 16

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSMAGIC_REQUIREMENTS = 0xFADE0C01
CSMAGIC_REQUIREMENT = 0xFADE0C00
CSMAGIC_EMBEDDED_ENTITLEMENTS = 0xFADE7171
CSMAGIC_EMBEDDED_DER_ENTITLEMENTS = 0xFADE7172
CSMAGIC_BLOBWRAPPER = 0xFADE0B01

CSSLOT_CODEDIRECTORY = 0
CSSLOT_REQUIREMENTS = 2
CSSLOT_ENTITLEMENTS = 5
CSSLOT_ENTITLEMENTS_DER = 7
CSSLOT_SIGNATURE = 0x10000

CS_ADHOC = 0x00000002

CD_VERSION = 0x20400
CD_HEADER_SIZE = 88
HASH_TYPE_SHA256 = 2
HASH_SIZE_SHA256 = 32
PAGE_SIZE_LOG2 = 12
PAGE_SIZE = 1 << PAGE_SIZE_LOG2

SIGNATURE_ALIGNMENT = 16
LINKEDIT_VM_ALIGNMENT = 0x4000

CS_EXECSEG_MAIN_BINARY = 0x1
CS_EXECSEG_ALLOW_UNSIGNED = 0x10

OP_FALSE = 0
OP_IDENT = 2
OP_AND = 6
OP_CERT_FIELD = 11
OP_CERT_GENERIC = 14
OP_APPLE_GENERIC_ANCHOR = 15
MATCH_EXISTS = 0
MATCH_EQUAL = 1
WWDR_INTERMEDIATE_MARKER = "1.2.840.113635.100.6.2.1"

SPECIAL_SLOT_INFO = 1
SPECIAL_SLOT_REQUIREMENTS = 2
SPECIAL_SLOT_RESOURCES = 3
SPECIAL_SLOT_ENTITLEMENTS = 5
SPECIAL_SLOT_ENTITLEMENTS_DER = 7


def align_to(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def blob(magic: int, payload: bytes) -> bytes:
    return struct.pack(">II", magic, len(payload) + 8) + payload


def build_superblob(blobs: Sequence[tuple[int, bytes]]) -> bytes:
    ordered = sorted(blobs, key=lambda item: item[0])
    header_size = 12 + len(ordered) * 8
    index = b""
    payload = b""
    for slot, b in ordered:
        index += struct.pack(">II", slot, header_size + len(payload))
        payload += b
    total = header_size + len(payload)
    return struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, total, len(ordered)) + index + payload


def _requirement_data(value: bytes) -> bytes:
    padding = (-len(value)) % 4
    return struct.pack(">I", len(value)) + value + b"\x00" * padding


def _oid_body(dotted: str) -> bytes:
    encoded = asn1.encode_oid(dotted)
    return asn1.parse_one(encoded).value


def build_requirements_blob(
    identifier: str | None = None,
    identity: SigningIdentity | None = None,
) -> bytes:
    if identity is None:
        return blob(CSMAGIC_REQUIREMENTS, struct.pack(">I", 0))
    if not identifier:
        raise SigningError("a certificated requirement needs an identifier")
    common_name = identity.certificate.common_name
    if not common_name:
        raise SigningError("signing certificate has no common name")

    expression = b"".join([
        struct.pack(">I", OP_AND),
        struct.pack(">I", OP_IDENT),
        _requirement_data(identifier.encode("utf-8")),
        struct.pack(">I", OP_AND),
        struct.pack(">I", OP_APPLE_GENERIC_ANCHOR),
        struct.pack(">I", OP_AND),
        struct.pack(">Ii", OP_CERT_FIELD, 0),
        _requirement_data(b"subject.CN"),
        struct.pack(">I", MATCH_EQUAL),
        _requirement_data(common_name.encode("utf-8")),
        struct.pack(">Ii", OP_CERT_GENERIC, 1),
        _requirement_data(_oid_body(WWDR_INTERMEDIATE_MARKER)),
        struct.pack(">I", MATCH_EXISTS),
    ])
    req = blob(CSMAGIC_REQUIREMENT, struct.pack(">I", 1) + expression)
    header_size = 12 + 8
    payload = struct.pack(">I", 1) + struct.pack(">II", 3, header_size) + req
    return blob(CSMAGIC_REQUIREMENTS, payload)


# wraps macho.parse to raise SigningError instead
def parse_macho(data: bytes) -> dict[str, Any]:
    try:
        return macho.parse(data)
    except MachOError as err:
        raise SigningError(str(err)) from err


def code_page_hashes(content: bytes, code_limit: int) -> list[bytes]:
    hashes: list[bytes] = []
    for start in range(0, code_limit, PAGE_SIZE):
        chunk = content[start : min(start + PAGE_SIZE, code_limit)]
        hashes.append(hashlib.sha256(chunk).digest())
    return hashes


def build_code_directory(
    identifier: str,
    code_limit: int,
    content: bytes,
    special_hashes: dict[int, bytes],
    exec_seg_limit: int,
    exec_seg_flags: int,
    flags: int = CS_ADHOC,
    team_id: str | None = None,
    platform: int = 0,
) -> bytes:
    identifier_bytes = identifier.encode("utf-8") + b"\x00"
    team_bytes = (team_id.encode("utf-8") + b"\x00") if team_id else b""
    n_special = max(special_hashes) if special_hashes else 0
    code_hashes = code_page_hashes(content, code_limit)

    ident_offset = CD_HEADER_SIZE
    team_offset = ident_offset + len(identifier_bytes) if team_bytes else 0
    hash_offset = (
        ident_offset + len(identifier_bytes) + len(team_bytes) + n_special * HASH_SIZE_SHA256
    )
    length = hash_offset + len(code_hashes) * HASH_SIZE_SHA256

    header = struct.pack(
        ">IIIIIIIIIBBBBIIII",
        CSMAGIC_CODEDIRECTORY,
        length,
        CD_VERSION,
        flags,
        hash_offset,
        ident_offset,
        n_special,
        len(code_hashes),
        code_limit,
        HASH_SIZE_SHA256,
        HASH_TYPE_SHA256,
        platform,
        PAGE_SIZE_LOG2,
        0,
        0,
        team_offset,
        0,
    )
    header += struct.pack(">QQQQ", 0, 0, exec_seg_limit, exec_seg_flags)
    if len(header) != CD_HEADER_SIZE:
        raise SigningError(
            f"CodeDirectory header is {len(header)} bytes, expected {CD_HEADER_SIZE}"
        )

    slots = b"".join(
        special_hashes.get(index, b"\x00" * HASH_SIZE_SHA256)
        for index in range(n_special, 0, -1)
    )
    return header + identifier_bytes + team_bytes + slots + b"".join(code_hashes)


def cdhash(signed: bytes) -> bytes:
    info = parse_macho(signed)
    if not info["signature"]:
        raise SigningError("binary is not signed")
    start = info["signature"]["dataoff"]
    s_blob = signed[start : start + info["signature"]["datasize"]]
    count = struct.unpack_from(">I", s_blob, 8)[0]
    for index in range(count):
        slot, offset = struct.unpack_from(">II", s_blob, 12 + index * 8)
        if slot == CSSLOT_CODEDIRECTORY:
            length = struct.unpack_from(">I", s_blob, offset + 4)[0]
            return hashlib.sha256(s_blob[offset : offset + length]).digest()
    raise SigningError("signature has no CodeDirectory")


def verify_signature(data: bytes) -> list[str]:
    from iosc.codesign import cms

    problems: list[str] = []
    try:
        info = parse_macho(data)
    except SigningError as exc:
        return [str(exc)]
    signature_cmd = info["signature"]
    if not signature_cmd:
        return ["binary has no LC_CODE_SIGNATURE"]

    dataoff, datasize = signature_cmd["dataoff"], signature_cmd["datasize"]
    if dataoff % SIGNATURE_ALIGNMENT:
        problems.append(f"dataoff {dataoff} is not 16 byte aligned")
    if dataoff + datasize != len(data):
        problems.append(f"dataoff+datasize is {dataoff + datasize}, file is {len(data)} bytes")
    linkedit = info["segments"].get("__LINKEDIT")
    if linkedit and linkedit["fileoff"] + linkedit["filesize"] != dataoff + datasize:
        problems.append("__LINKEDIT does not end where the signature ends")

    s_blob = data[dataoff : dataoff + datasize]
    if len(s_blob) < 12 or struct.unpack_from(">I", s_blob, 0)[0] != CSMAGIC_EMBEDDED_SIGNATURE:
        problems.append("SuperBlob magic is wrong")
        return problems

    count = struct.unpack_from(">I", s_blob, 8)[0]
    directory = None
    signature = None
    for index in range(count):
        slot, offset = struct.unpack_from(">II", s_blob, 12 + index * 8)
        if offset + 8 > len(s_blob):
            problems.append(f"slot {slot:#x} points past the end of the signature")
            continue
        length = struct.unpack_from(">I", s_blob, offset + 4)[0]
        if slot == CSSLOT_CODEDIRECTORY:
            directory = s_blob[offset : offset + length]
        elif slot == CSSLOT_SIGNATURE and length > 8:
            signature = s_blob[offset + 8 : offset + length]
    if directory is None:
        problems.append("no CodeDirectory in the SuperBlob")
        return problems

    flags = struct.unpack_from(">I", directory, 0x0C)[0]
    if signature is None:
        if not (flags & CS_ADHOC):
            problems.append("signature has no CMS blob and is not marked ad-hoc")
    else:
        if flags & CS_ADHOC:
            problems.append("signature carries a CMS blob but is marked ad-hoc")
        problems.extend(cms.verify_signature(signature, directory))

    hash_offset, ident_offset, n_special, n_code = struct.unpack_from(">IIII", directory, 0x10)
    code_limit = struct.unpack_from(">I", directory, 0x20)[0]
    if code_limit != dataoff:
        problems.append(f"codeLimit {code_limit} does not equal dataoff {dataoff}")

    expected = code_page_hashes(data, code_limit)
    if len(expected) != n_code:
        problems.append(f"nCodeSlots is {n_code}, recomputed {len(expected)}")
    for index, digest in enumerate(expected[:n_code]):
        start = hash_offset + index * HASH_SIZE_SHA256
        if directory[start : start + HASH_SIZE_SHA256] != digest:
            problems.append(f"page hash {index} does not match")
            break
    return problems
