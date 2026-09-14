from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
import uuid as uuid_module

from iosc.core import process
from iosc.core.errors import DebugError, ExternalToolError
from iosc.formats import macho


# an .ips report is a json header line, a newline, then a json body
# the body holds usedImages and threads
def parse_ips(text: str) -> dict[str, Any]:
    header_line, separator, body_text = text.partition("\n")
    if not separator:
        raise DebugError("not a two part .ips file, no header line found")
    try:
        header = json.loads(header_line)
    except json.JSONDecodeError as error:
        raise DebugError(f"ips header is not valid JSON: {error}") from error
    try:
        body = json.loads(body_text)
    except json.JSONDecodeError as error:
        raise DebugError(f"ips body is not valid JSON: {error}") from error
    return {"header": header, "body": body}


def _normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid_module.UUID(value)).upper()
    except ValueError:
        return None


def _macho_uuid(raw: bytes) -> str:
    return str(uuid_module.UUID(bytes=raw)).upper()


# map each binary uuid to its parsed mach-o
# lets a frame's image resolve regardless of input order
def load_images(binaries: list[bytes]) -> dict[str, dict[str, Any]]:
    images = {}
    for data in binaries:
        parsed = macho.parse(data)
        if parsed["uuid"] is not None:
            images[_macho_uuid(parsed["uuid"])] = parsed
    return images


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


# maps each binary's uuid to its on disk path, for llvm-symbolizer
# load_images reads bytes for the symbol table, this keeps the paths that
# actually carry debug info (the app binary or a dsym's DWARF file)
def index_binary_paths(binary_paths: list[Path]) -> dict[str, Path]:
    indexed = {}
    for path in binary_paths:
        parsed = macho.parse(path.read_bytes())
        if parsed["uuid"] is not None:
            indexed[_macho_uuid(parsed["uuid"])] = path
    return indexed


def symbolize_location(llvm_symbolizer: Path, binary_path: Path, vmaddr: int) -> str | None:
    try:
        result = process.run(
            [str(llvm_symbolizer), "-e", str(binary_path), "-f", "-C", hex(vmaddr)]
        )
    except ExternalToolError:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    location = lines[1]
    if location.startswith("??"):
        return None
    return location


@dataclass(frozen=True)
class ResolvedFrame:
    index: int
    image_name: str
    symbol: str | None
    offset: int
    resolved: bool
    location: str | None = None


# imageOffset is relative to the image base
# add the static __TEXT vmaddr from the linked mach-o to match the symbol table
# no aslr slide needed
def symbolicate_thread(
    body: dict[str, Any],
    thread_index: int,
    images: dict[str, dict[str, Any]],
    binary_paths: dict[str, Path] | None = None,
    llvm_symbolizer: Path | None = None,
) -> list[ResolvedFrame]:
    used_images = body.get("usedImages", [])
    threads = body.get("threads", [])
    if thread_index < 0 or thread_index >= len(threads):
        raise DebugError(f"no thread {thread_index}, report has {len(threads)}")

    resolved = []
    for position, frame in enumerate(threads[thread_index].get("frames", [])):
        image_index = frame.get("imageIndex")
        image_offset = frame.get("imageOffset", 0)
        image = (
            used_images[image_index]
            if image_index is not None and 0 <= image_index < len(used_images)
            else None
        )
        image_name = "?"
        parsed = None
        if image is not None:
            image_name = image.get("name") or _basename(image.get("path", "?"))
            image_uuid = _normalize_uuid(image.get("uuid"))
            parsed = images.get(image_uuid) if image_uuid else None

        if parsed is None:
            resolved.append(
                ResolvedFrame(position, image_name, None, image_offset, False)
            )
            continue

        text_vmaddr = parsed["segments"].get("__TEXT", {}).get("vmaddr", 0)
        vmaddr = text_vmaddr + image_offset
        match = macho.nearest_symbol(parsed, vmaddr)
        if match is None:
            resolved.append(
                ResolvedFrame(position, image_name, None, image_offset, False)
            )
        else:
            symbol, offset = match
            location = None
            binary_path = binary_paths.get(image_uuid) if binary_paths else None
            if binary_path is not None and llvm_symbolizer is not None:
                location = symbolize_location(llvm_symbolizer, binary_path, vmaddr)
            resolved.append(
                ResolvedFrame(position, image_name, symbol, offset, True, location)
            )
    return resolved


def format_backtrace(frames: list[ResolvedFrame]) -> str:
    lines = []
    for frame in frames:
        if frame.resolved:
            tail = f" ({frame.location})" if frame.location else ""
            lines.append(
                f"{frame.index:<3} {frame.image_name:<24} "
                f"{frame.symbol} + {frame.offset:#x}{tail}"
            )
        else:
            lines.append(
                f"{frame.index:<3} {frame.image_name:<24} "
                f"0x{frame.offset:x} (no symbol, binary not provided or UUID mismatch)"
            )
    return "\n".join(lines)


def symbolicate_crash(
    ips_text: str,
    binaries: list[bytes],
    thread_index: int | None = None,
    binary_paths: list[Path] | None = None,
    llvm_symbolizer: Path | None = None,
) -> str:
    parsed = parse_ips(ips_text)
    body = parsed["body"]
    images = load_images(binaries)
    indexed_paths = index_binary_paths(binary_paths) if binary_paths else None
    if thread_index is None:
        thread_index = body.get("faultingThread", 0)
    frames = symbolicate_thread(
        body, thread_index, images, indexed_paths, llvm_symbolizer
    )
    return format_backtrace(frames)
