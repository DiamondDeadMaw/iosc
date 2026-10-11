import struct

from iosc.core import SimulatorError
from iosc.formats.png import encode_png

BMP_HEADER_SIZE = 54


# iossim writes top down 32 bit BGRA. its alpha is not required
def bmp_to_png(data: bytes) -> bytes:
    if len(data) < BMP_HEADER_SIZE or data[:2] != b"BM":
        raise SimulatorError("the simulator screenshot is not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    if bits != 32 or width <= 0 or height == 0:
        raise SimulatorError(f"unsupported screenshot BMP, {bits} bit {width}x{height}")
    rows = abs(height)
    stride = width * 4
    pixels = data[offset:offset + stride * rows]
    if len(pixels) != stride * rows:
        raise SimulatorError("the simulator screenshot is truncated")
    if height > 0:
        pixels = b"".join(
            pixels[row * stride:(row + 1) * stride] for row in reversed(range(rows))
        )
    rgba = bytearray(pixels)
    rgba[0::4], rgba[2::4] = pixels[2::4], pixels[0::4]
    rgba[3::4] = b"\xff" * (width * rows)
    return encode_png(bytes(rgba), width, rows)
