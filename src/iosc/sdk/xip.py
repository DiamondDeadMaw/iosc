import collections
from collections.abc import Callable
import concurrent.futures
from dataclasses import dataclass, field
import io
import lzma
import os
from pathlib import Path
import struct
from typing import Any, BinaryIO, Iterator
import xml.etree.ElementTree as ET
import zlib

from iosc.core.errors import SdkError

__all__ = [
    "Entry",
    "ProgressCallback",
    "XipError",
    "BadMagicError",
    "iter_entries",
]

# called with compressed bytes consumed and the total, both known up front
ProgressCallback = Callable[[int, int], None]


class XipError(SdkError):
    pass


class BadMagicError(XipError):
    def __init__(
        self,
        layer: str,
        offset: int,
        expected: bytes | tuple[bytes, ...],
        actual: bytes,
    ) -> None:
        self.layer = layer
        self.offset = offset
        self.expected = expected
        self.actual = actual
        exp_str = (
            repr(expected)
            if isinstance(expected, bytes)
            else ", ".join(repr(e) for e in expected)
        )
        super().__init__(
            f"Bad magic at {layer} layer (offset {offset}) expected {exp_str}, got {actual!r}"
        )


@dataclass
class Entry:
    name: str
    mode: int
    size: int
    kind: str
    dev: int | tuple[int, int] = 0
    ino: int = 0
    nlink: int = 1
    _stream: "_Stream | None" = field(default=None, repr=False, compare=False)
    _data_cache: bytes | None = field(default=None, repr=False, compare=False)
    _consumed: bool = field(default=False, repr=False, compare=False)

    def data(self) -> bytes:
        if self._data_cache is not None:
            return self._data_cache
        if self.kind == "dir" or self.size == 0:
            self._data_cache = b""
            return self._data_cache
        if self._consumed:
            raise SdkError(f"Data for '{self.name}' has already been consumed or skipped")
        if self._stream is None:
            raise SdkError("Stream is not available for reading entry data")
        data_bytes = self._stream.read(self.size)
        if len(data_bytes) < self.size:
            raise XipError(
                f"Unexpected EOF reading data for '{self.name}' expected {self.size} bytes, got {len(data_bytes)}"
            )
        self._data_cache = data_bytes
        self._consumed = True
        return self._data_cache

    def chunks(self, block_size: int = 1 << 20) -> Iterator[bytes]:
        if self._consumed:
            raise SdkError(f"Data for '{self.name}' has already been consumed or skipped")
        if self._stream is None and self.size > 0:
            raise SdkError("Stream is not available for reading entry data")
        if block_size <= 0:
            raise SdkError("block_size must be positive")
        self._consumed = True
        return self._iter_chunks(block_size)

    def _iter_chunks(self, block_size: int) -> Iterator[bytes]:
        if self.kind == "dir" or self.size == 0:
            return
        remaining = self.size
        while remaining > 0:
            to_read = min(block_size, remaining)
            block = self._stream.read(to_read)
            if len(block) < to_read:
                raise XipError(
                    f"Unexpected EOF reading data for '{self.name}' expected {to_read} bytes, got {len(block)}"
                )
            remaining -= len(block)
            yield block

    def copy_to(self, dst: Any, block_size: int = 1 << 20) -> int:
        total = 0
        for block in self.chunks(block_size=block_size):
            dst.write(block)
            total += len(block)
        return total


class _Stream:
    def __init__(self, gen: Iterator[bytes]) -> None:
        self._gen = gen
        self._chunks: collections.deque[bytes] = collections.deque()
        self._offset: int = 0
        self._available: int = 0
        self.pos: int = 0
        self._eof: bool = False

    def _pull(self, min_bytes: int) -> None:
        while self._available < min_bytes and not self._eof:
            try:
                chunk = next(self._gen)
                if chunk:
                    self._chunks.append(chunk)
                    self._available += len(chunk)
            except StopIteration:
                self._eof = True

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        self._pull(n)
        if not self._chunks:
            return b""

        first = self._chunks[0]
        avail_in_first = len(first) - self._offset
        if avail_in_first >= n:
            res = first[self._offset : self._offset + n]
            self._offset += n
            self._available -= n
            self.pos += len(res)
            if self._offset == len(first):
                self._chunks.popleft()
                self._offset = 0
            return res

        parts: list[bytes] = []
        needed = n
        while self._chunks and needed > 0:
            chunk = self._chunks[0]
            avail = len(chunk) - self._offset
            take = min(avail, needed)
            parts.append(chunk[self._offset : self._offset + take])
            self._offset += take
            self._available -= take
            self.pos += take
            needed -= take
            if self._offset == len(chunk):
                self._chunks.popleft()
                self._offset = 0
        return b"".join(parts)

    def skip(self, n: int) -> None:
        if n <= 0:
            return

        while self._chunks and n > 0:
            chunk = self._chunks[0]
            avail = len(chunk) - self._offset
            if avail <= n:
                self._chunks.popleft()
                self._offset = 0
                self._available -= avail
                self.pos += avail
                n -= avail
            else:
                self._offset += n
                self._available -= n
                self.pos += n
                n = 0
                return

        while n > 0 and not self._eof:
            try:
                chunk = next(self._gen)
            except StopIteration:
                self._eof = True
                break
            if not chunk:
                continue
            chunk_len = len(chunk)
            if chunk_len <= n:
                n -= chunk_len
                self.pos += chunk_len
            else:
                self._chunks.append(chunk)
                self._offset = n
                self._available = chunk_len - n
                self.pos += n
                n = 0
                break


def _align(stream: _Stream, boundary: int) -> None:
    if boundary > 1:
        pad = (-stream.pos) % boundary
        if pad:
            stream.skip(pad)


def _decompress_chunk(blob: bytes) -> bytes:
    if blob.startswith(b"\xfd7zXZ\x00"):
        return lzma.decompress(blob)
    return blob


def _pbzx_stream(
    fh: BinaryIO,
    total_length: int,
    base_offset: int,
    workers: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> Iterator[bytes]:
    if workers is None:
        workers = min(os.cpu_count() or 4, 8)
    magic = fh.read(4)
    if magic != b"pbzx":
        raise BadMagicError("pbzx", base_offset, b"pbzx", magic)

    raw_flags = fh.read(8)
    if len(raw_flags) < 8:
        raise XipError(f"Unexpected EOF reading pbzx flags at offset {base_offset + 4}")

    bytes_read = 12

    if workers <= 1:
        while bytes_read < total_length:
            hdr = fh.read(16)
            if not hdr:
                break
            if len(hdr) < 16:
                raise XipError(f"Truncated pbzx record header at offset {base_offset + bytes_read}")
            bytes_read += 16

            _uncomp, comp = struct.unpack(">QQ", hdr)
            blob = fh.read(comp)
            if len(blob) < comp:
                raise XipError(f"Truncated pbzx payload at offset {base_offset + bytes_read}")
            bytes_read += comp
            if on_progress is not None:
                on_progress(bytes_read, total_length)
            yield _decompress_chunk(blob)
        return

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        window: collections.deque[concurrent.futures.Future[bytes]] = collections.deque()
        while bytes_read < total_length:
            hdr = fh.read(16)
            if not hdr:
                break
            if len(hdr) < 16:
                raise XipError(f"Truncated pbzx record header at offset {base_offset + bytes_read}")
            bytes_read += 16

            _uncomp, comp = struct.unpack(">QQ", hdr)
            blob = fh.read(comp)
            if len(blob) < comp:
                raise XipError(f"Truncated pbzx payload at offset {base_offset + bytes_read}")
            bytes_read += comp
            if on_progress is not None:
                on_progress(bytes_read, total_length)

            fut = executor.submit(_decompress_chunk, blob)
            window.append(fut)
            if len(window) >= workers:
                yield window.popleft().result()

        while window:
            yield window.popleft().result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _read_cpio_header(
    stream: _Stream,
) -> tuple[int, int, int, int, int | tuple[int, int], int, int] | None:
    start_pos = stream.pos
    magic = stream.read(6)
    if not magic:
        return None
    if len(magic) < 6:
        raise BadMagicError("CPIO", start_pos, (b"070707", b"070701", b"070702"), magic)

    if magic == b"070707":
        raw = stream.read(70)
        if len(raw) < 70:
            raise XipError(f"Unexpected EOF reading odc CPIO header at offset {start_pos}")
        dev = int(raw[0:6], 8)
        ino = int(raw[6:12], 8)
        mode = int(raw[12:18], 8)
        nlink = int(raw[30:36], 8)
        namesize = int(raw[53:59], 8)
        filesize = int(raw[59:70], 8)
        return mode, filesize, namesize, 1, dev, ino, nlink

    if magic in (b"070701", b"070702"):
        raw = stream.read(104)
        if len(raw) < 104:
            raise XipError(f"Unexpected EOF reading newc CPIO header at offset {start_pos}")
        ino = int(raw[0:8], 16)
        mode = int(raw[8:16], 16)
        nlink = int(raw[32:40], 16)
        filesize = int(raw[48:56], 16)
        devmajor = int(raw[56:64], 16)
        devminor = int(raw[64:72], 16)
        dev = (devmajor, devminor)
        namesize = int(raw[88:96], 16)
        return mode, filesize, namesize, 4, dev, ino, nlink

    raise BadMagicError("CPIO", start_pos, (b"070707", b"070701", b"070702"), magic)


def _parse_cpio(stream: _Stream) -> Iterator[Entry]:
    while True:
        header = _read_cpio_header(stream)
        if header is None:
            break
        mode, filesize, namesize, boundary, dev, ino, nlink = header
        raw_name = stream.read(namesize)
        if len(raw_name) < namesize:
            raise XipError(f"Unexpected EOF reading filename at offset {stream.pos}")
        if raw_name.endswith(b"\x00"):
            raw_name = raw_name[:-1]
        name = raw_name.decode("utf-8", errors="replace")
        _align(stream, boundary)

        if name == "TRAILER!!!":
            break

        file_type = mode & 0o170000
        if file_type == 0o040000:
            kind = "dir"
        elif file_type == 0o120000:
            kind = "symlink"
        else:
            kind = "file"

        entry = Entry(
            name=name,
            mode=mode,
            size=filesize,
            kind=kind,
            dev=dev,
            ino=ino,
            nlink=nlink,
            _stream=stream,
        )
        data_start = stream.pos
        yield entry

        advanced = stream.pos - data_start
        if advanced < entry.size:
            stream.skip(entry.size - advanced)
        elif advanced > entry.size:
            raise XipError(
                f"Entry '{entry.name}' exceeded declared size expected {entry.size} bytes, advanced {advanced}"
            )
        entry._consumed = True
        _align(stream, boundary)


def _parse_xip(
    fh: BinaryIO,
    workers: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> Iterator[Entry]:
    raw_header = fh.read(28)
    if len(raw_header) < 28 or raw_header[:4] != b"xar!":
        actual_magic = raw_header[:4] if len(raw_header) >= 4 else raw_header
        raise BadMagicError("XAR", 0, b"xar!", actual_magic)

    _magic, header_size, _version, toc_compressed_len, _toc_uncompressed_len, _checksum = (
        struct.unpack(">4sHHQQI", raw_header)
    )
    fh.seek(header_size)
    compressed_toc = fh.read(toc_compressed_len)
    if len(compressed_toc) < toc_compressed_len:
        raise XipError("Truncated XAR table of contents")

    toc_xml = zlib.decompress(compressed_toc)
    toc = ET.fromstring(toc_xml)
    heap_start = header_size + toc_compressed_len

    content = None
    for file_elem in toc.iter("file"):
        if file_elem.findtext("name") == "Content":
            data_elem = file_elem.find("data")
            if data_elem is not None:
                off_text = data_elem.findtext("offset")
                len_text = data_elem.findtext("length")
                if off_text is not None and len_text is not None:
                    content = (int(off_text), int(len_text))
                    break

    if content is None:
        raise XipError("No 'Content' file entry found in XAR table of contents")

    content_offset = heap_start + content[0]
    content_length = content[1]
    fh.seek(content_offset)

    chunks = _pbzx_stream(
        fh, content_length, content_offset, workers=workers, on_progress=on_progress
    )
    stream = _Stream(chunks)
    yield from _parse_cpio(stream)


def iter_entries(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    workers: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> Iterator[Entry]:
    if isinstance(path, (str, os.PathLike)):
        with open(path, "rb") as fh:
            yield from _parse_xip(fh, workers=workers, on_progress=on_progress)
    else:
        yield from _parse_xip(path, workers=workers, on_progress=on_progress)
