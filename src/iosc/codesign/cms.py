from collections.abc import Sequence
import datetime
import hashlib
from typing import Any

from iosc.core.errors import SigningError
from iosc.formats import asn1, pki
from iosc.formats.pki import Certificate, SigningIdentity
from iosc.formats.plist import write_plist

OID_DATA = "1.2.840.113549.1.7.1"
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_TIME = "1.2.840.113549.1.9.5"

OID_APPLE_CD_HASHES = "1.2.840.113635.100.9.2"
OID_APPLE_CD_HASHES_PLIST = "1.2.840.113635.100.9.1"

CD_HASH_PLIST_LENGTH = 20

SIGNER_INFO_VERSION = 1
SIGNED_DATA_VERSION = 1


class CmsError(SigningError):
    pass


def _attribute(oid: str, *values: bytes) -> bytes:
    return asn1.encode_sequence(asn1.encode_oid(oid), asn1.encode_set_of(values))


def cd_hashes_attribute(digests: Sequence[bytes]) -> bytes:
    return _attribute(
        OID_APPLE_CD_HASHES,
        *[
            asn1.encode_sequence(
                asn1.encode_oid(pki.OID_SHA256),
                asn1.encode_octet_string(d),
            )
            for d in digests
        ],
    )


def cd_hashes_plist_attribute(digests: Sequence[bytes]) -> bytes:
    payload = write_plist(
        {"cdhashes": [d[:CD_HASH_PLIST_LENGTH] for d in digests]},
        binary=False,
    )
    return _attribute(OID_APPLE_CD_HASHES_PLIST, asn1.encode_octet_string(payload))


def build_signed_attributes(
    code_directories: Sequence[bytes],
    signing_time: datetime.datetime | None = None,
) -> bytes:
    if not code_directories:
        raise CmsError("a signature needs at least one CodeDirectory")
    digests = [hashlib.sha256(cd).digest() for cd in code_directories]
    attributes = [
        _attribute(OID_CONTENT_TYPE, asn1.encode_oid(OID_DATA)),
        _attribute(OID_MESSAGE_DIGEST, asn1.encode_octet_string(digests[0])),
        cd_hashes_attribute(digests),
        cd_hashes_plist_attribute(digests),
    ]
    if signing_time is not None:
        attributes.append(
            _attribute(OID_SIGNING_TIME, asn1.encode_utc_time(signing_time))
        )
    return asn1.encode_set_of(attributes)


def build_signer_info(identity: SigningIdentity, signed_attributes: bytes) -> bytes:
    signature = identity.key.sign(signed_attributes)
    return asn1.encode_sequence(
        asn1.encode_integer(SIGNER_INFO_VERSION),
        identity.certificate.issuer_and_serial(),
        pki.sha256_algorithm(),
        asn1.encode_implicit(0, signed_attributes),
        pki.sha256_with_rsa_algorithm(),
        asn1.encode_octet_string(signature),
    )


def build_signature(
    identity: SigningIdentity,
    code_directories: Sequence[bytes],
    signing_time: datetime.datetime | None = None,
) -> bytes:
    signed_attributes = build_signed_attributes(code_directories, signing_time)
    signer = build_signer_info(identity, signed_attributes)
    certificates = b"".join(sorted(c.der for c in identity.chain))
    signed_data = asn1.encode_sequence(
        asn1.encode_integer(SIGNED_DATA_VERSION),
        asn1.encode_set(pki.sha256_algorithm()),
        asn1.encode_sequence(asn1.encode_oid(OID_DATA)),
        asn1.encode(asn1.context(0), certificates),
        asn1.encode_set(signer),
    )
    return asn1.encode_sequence(
        asn1.encode_oid(OID_SIGNED_DATA),
        asn1.encode_explicit(0, signed_data),
    )


def parse_signature(data: bytes) -> dict[str, Any]:
    root = asn1.parse_one(data)
    if root.children[0].as_oid() != OID_SIGNED_DATA:
        raise CmsError("content is not CMS SignedData")
    signed_data = root.children[1].children[0]
    members = signed_data.children
    certificates: list[Certificate] = []
    signers = None
    for part in members[2:]:
        if part.context_number == 0 and part.constructed:
            certificates = [Certificate(c.raw) for c in part.children]
        elif part.tag == asn1.SET:
            signers = part
    if signers is None or not signers.children:
        raise CmsError("signature has no SignerInfo")
    signer = signers.children[0]
    signed_attributes = None
    for part in signer.children:
        if part.context_number == 0:
            signed_attributes = part
    if signed_attributes is None:
        raise CmsError("SignerInfo carries no signed attributes")
    attributes: dict[str, Any] = {}
    for attribute in signed_attributes.children:
        attributes[attribute.children[0].as_oid()] = attribute.children[1].children
    return {
        "certificates": certificates,
        "attributes": attributes,
        "signed_bytes": asn1.encode(asn1.SET, signed_attributes.value),
        "signature": signer.children[-1].value,
        "message_digest": attributes[OID_MESSAGE_DIGEST][0].value,
    }


def verify_signature(data: bytes, code_directory: bytes | None = None) -> list[str]:
    problems: list[str] = []
    try:
        parsed = parse_signature(data)
    except (CmsError, asn1.Asn1Error, IndexError) as exc:
        return [f"unreadable signature ({exc})"]

    signed = False
    for certificate in parsed["certificates"]:
        if certificate.verify(parsed["signed_bytes"], parsed["signature"]):
            signed = True
            break
    if not signed:
        problems.append("no certificate in the signature verifies it")

    if code_directory is not None:
        expected = hashlib.sha256(code_directory).digest()
        if parsed["message_digest"] != expected:
            problems.append("messageDigest does not match the CodeDirectory")
        hashes = parsed["attributes"].get(OID_APPLE_CD_HASHES)
        if hashes:
            found = [
                n.value for n in hashes[0].children
                if n.tag == asn1.OCTET_STRING
            ]
            if expected not in found:
                problems.append("cdHashes attribute does not list the CodeDirectory")
    return problems
