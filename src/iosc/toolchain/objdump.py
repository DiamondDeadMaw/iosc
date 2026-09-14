from dataclasses import dataclass
from pathlib import Path

from iosc.core import ExternalToolError, LinkError, process
from iosc.toolchain.discovery import Toolchain


@dataclass(frozen=True)
class MachOSummary:
    filetype: str
    flags: list[str]
    arch: str
    platform: str
    load_dylibs: list[str]


def parse_macho_headers(output: str) -> MachOSummary:
    filetype = ""
    flags: list[str] = []
    arch = ""
    platform = ""
    load_dylibs: list[str] = []

    lines = output.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "Mach header":
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                tokens = lines[i].strip().split()
                if len(tokens) >= 5:
                    arch = tokens[1]
                    filetype = tokens[4]
                    if len(tokens) > 7:
                        flags = [t for t in tokens[7:] if t != "(none)"]
        i += 1

    current_cmd = ""
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("cmd "):
            current_cmd = line[4:].strip()
            if current_cmd == "LC_VERSION_MIN_IPHONEOS":
                platform = "ios"
            elif current_cmd == "LC_VERSION_MIN_MACOSX":
                platform = "macos"
            elif current_cmd == "LC_VERSION_MIN_TVOS":
                platform = "tvos"
            elif current_cmd == "LC_VERSION_MIN_WATCHOS":
                platform = "watchos"
        elif current_cmd == "LC_BUILD_VERSION":
            if line.startswith("platform "):
                platform = line.split()[1]
        elif current_cmd == "LC_LOAD_DYLIB":
            if line.startswith("name "):
                name_part = line[5:].strip()
                clean_name = name_part.split(" (offset ")[0].strip()
                load_dylibs.append(clean_name)

    return MachOSummary(
        filetype=filetype,
        flags=flags,
        arch=arch,
        platform=platform,
        load_dylibs=load_dylibs,
    )


def inspect(binary: Path, toolchain: Toolchain) -> MachOSummary:
    objdump = toolchain.swift_bin_dir / "llvm-objdump.exe"
    if not objdump.is_file():
        objdump = toolchain.swift_bin_dir / "llvm-objdump"
    argv = [str(objdump), "--macho", "--all-headers", str(binary)]
    try:
        proc = process.run(argv)
    except ExternalToolError as err:
        msg = err.stderr if err.stderr else str(err)
        raise LinkError(msg) from err
    return parse_macho_headers(proc.stdout)


def assert_linked_ok(summary: MachOSummary) -> None:
    if summary.filetype != "EXECUTE":
        raise LinkError(
            f"Expected Mach-O filetype EXECUTE, found {summary.filetype}"
        )
    if "NOUNDEFS" not in summary.flags:
        raise LinkError(
            "Mach-O binary missing NOUNDEFS flag, undefined symbols present"
        )
