import bisect
from dataclasses import dataclass
import struct
from typing import Any

from iosc.core.errors import MachOError

MH_MAGIC_64 = 0xFEEDFACF
# MH_MAGIC_64 as it sits at the head of a little endian file
MH_MAGIC_64_BYTES = b"\xcf\xfa\xed\xfe"
FAT_MAGICS = (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")

CPU_TYPE_ARM64 = 0x0100000C

MH_EXECUTE = 2
MH_DYLIB = 6
MH_BUNDLE = 8

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_CODE_SIGNATURE = 0x1D
LC_UUID = 0x1B

HEADER_SIZE = 32
SEGMENT_NAME_SIZE = 16
SECTION_SIZE = 80
SECTION_OFFSET_FIELD = 48
NLIST_64_SIZE = 16
N_STAB = 0xE0


@dataclass(frozen=True)
class Header:
    cputype: int
    cpusubtype: int
    filetype: int
    ncmds: int
    sizeofcmds: int
    flags: int

    @property
    def is_arm64(self) -> bool:
        return self.cputype == CPU_TYPE_ARM64

    @property
    def is_executable(self) -> bool:
        return self.filetype == MH_EXECUTE


def read_header(data: bytes) -> Header:
    if data[:4] in FAT_MAGICS:
        raise MachOError("fat binaries are not supported yet, pass a thin slice")
    if len(data) < HEADER_SIZE:
        raise MachOError(f"file is {len(data)} bytes, too small to be a Mach-O")
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _reserved = (
        struct.unpack_from("<IiiIIIII", data, 0)
    )
    if magic != MH_MAGIC_64:
        raise MachOError(
            f"not a 64 bit little endian Mach-O, magic {magic:#x} "
            f"where {MH_MAGIC_64:#x} was expected"
        )
    return Header(cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags)


def require_arm64_executable(data: bytes) -> Header:
    header = read_header(data)
    if not header.is_arm64:
        raise MachOError(
            f"expected arm64 ({CPU_TYPE_ARM64:#x}), got {header.cputype:#x}"
        )
    if not header.is_executable:
        raise MachOError(f"expected an executable, got filetype {header.filetype}")
    return header


# read the nlist_64 table. resolve names via the string table, drop stab and undefined
def _read_symtab(
    data: bytes, symoff: int, nsyms: int, stroff: int
) -> list[dict[str, Any]]:
    symbols = []
    for index in range(nsyms):
        entry = symoff + index * NLIST_64_SIZE
        n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from(
            "<IBBHQ", data, entry
        )
        if n_type & N_STAB:
            continue
        if n_value == 0:
            continue
        name_start = stroff + n_strx
        name_end = data.index(b"\x00", name_start)
        name = data[name_start:name_end].decode("utf-8", "replace")
        symbols.append({"name": name, "address": n_value})
    symbols.sort(key=lambda symbol: symbol["address"])
    return symbols


# symbol nearest at or below vmaddr, for a "function + offset" when theres no
# dwarf line table
def nearest_symbol(parsed: dict[str, Any], vmaddr: int) -> tuple[str, int] | None:
    symbols = parsed["symbols"]
    addresses = [symbol["address"] for symbol in symbols]
    index = bisect.bisect_right(addresses, vmaddr) - 1
    if index < 0:
        return None
    symbol = symbols[index]
    return symbol["name"], vmaddr - symbol["address"]


# one walk of the load commands
# signing needs every segment, the signature cmd, the first section offset
# symbolication needs the uuid and symbol table
def parse(data: bytes) -> dict[str, Any]:
    header = read_header(data)
    info: dict[str, Any] = {
        "filetype": header.filetype,
        "ncmds": header.ncmds,
        "sizeofcmds": header.sizeofcmds,
        "segments": {},
        "signature": None,
        "first_section_offset": None,
        "uuid": None,
        "symbols": [],
    }

    offset = HEADER_SIZE
    for _ in range(header.ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmdsize < 8:
            raise MachOError(f"load command at {offset} has cmdsize {cmdsize}")

        if cmd == LC_SEGMENT_64:
            name = (
                data[offset + 8 : offset + 8 + SEGMENT_NAME_SIZE]
                .rstrip(b"\x00")
                .decode("ascii", "replace")
            )
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(
                "<QQQQ", data, offset + 24
            )
            nsects = struct.unpack_from("<I", data, offset + 64)[0]
            info["segments"][name] = {
                "vmaddr": vmaddr,
                "vmsize": vmsize,
                "fileoff": fileoff,
                "filesize": filesize,
                "command_offset": offset,
            }
            for index in range(nsects):
                section = offset + 72 + index * SECTION_SIZE
                section_offset = struct.unpack_from(
                    "<I", data, section + SECTION_OFFSET_FIELD
                )[0]
                if section_offset and (
                    info["first_section_offset"] is None
                    or section_offset < info["first_section_offset"]
                ):
                    info["first_section_offset"] = section_offset

        elif cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from("<II", data, offset + 8)
            info["signature"] = {
                "dataoff": dataoff,
                "datasize": datasize,
                "command_offset": offset,
            }

        elif cmd == LC_UUID:
            info["uuid"] = data[offset + 8 : offset + 24]

        elif cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            info["symbols"] = _read_symtab(data, symoff, nsyms, stroff)

        offset += cmdsize
    return info
