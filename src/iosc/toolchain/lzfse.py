from pathlib import Path
import tempfile

from iosc.config.paths import vendor_lzfse_dir
from iosc.core.errors import ToolchainError
from iosc.core import process
from iosc.formats.car import Decompressors


# framed streams start w/ bvx1/bvx2/bvxn/bvxr
# given anything else the tool reads a junk length
FRAMED_MAGIC = (b"bvx1", b"bvx2", b"bvxn", b"bvxr", b"bvx-")

DECODE_TIMEOUT = 60.0


def decode_lzfse_framed(compressed: bytes) -> bytes:
    tool = vendor_lzfse_dir() / "lzfse.exe"
    if not tool.exists():
        raise ToolchainError(f"lzfse binary missing at {tool}")
    if compressed[:4] not in FRAMED_MAGIC:
        raise ToolchainError(
            f"not an lzfse framed stream, magic {compressed[:4]!r}"
        )
    with tempfile.TemporaryDirectory() as d:
        temp_dir = Path(d)
        in_path = temp_dir / "in.bin"
        out_path = temp_dir / "out.bin"
        in_path.write_bytes(compressed)
        process.run(
            [str(tool), "-decode", "-i", str(in_path), "-o", str(out_path)],
            timeout=DECODE_TIMEOUT,
        )
        return out_path.read_bytes()


ENCODE_TIMEOUT = 60.0


def encode_lzfse_framed(raw: bytes) -> bytes:
    tool = vendor_lzfse_dir() / "lzfse.exe"
    if not tool.exists():
        raise ToolchainError(f"lzfse binary missing at {tool}")
    with tempfile.TemporaryDirectory() as d:
        temp_dir = Path(d)
        in_path = temp_dir / "in.bin"
        out_path = temp_dir / "out.bin"
        in_path.write_bytes(raw)
        process.run(
            [str(tool), "-encode", "-i", str(in_path), "-o", str(out_path)],
            timeout=ENCODE_TIMEOUT,
        )
        return out_path.read_bytes()


def decode_lzvn_raw(compressed: bytes, max_output_size: int) -> bytes:
    tool = vendor_lzfse_dir() / "lzvn_raw.exe"
    if not tool.exists():
        raise ToolchainError(f"lzvn_raw binary missing at {tool}")
    with tempfile.TemporaryDirectory() as d:
        temp_dir = Path(d)
        in_path = temp_dir / "in.bin"
        out_path = temp_dir / "out.bin"
        in_path.write_bytes(compressed)
        process.run(
            [str(tool), str(in_path), str(out_path), str(max_output_size)],
            timeout=DECODE_TIMEOUT,
        )
        return out_path.read_bytes()


def default_decompressors() -> Decompressors:
    return Decompressors(
        lzfse_framed=decode_lzfse_framed,
        lzvn_raw=decode_lzvn_raw,
    )
