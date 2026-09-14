import struct
from typing import Any
import zlib

from iosc.core.errors import PngError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

COLOR_GRAY = 0
COLOR_RGB = 2
COLOR_PALETTE = 3
COLOR_GRAY_ALPHA = 4
COLOR_RGBA = 6

CHANNELS = {
    COLOR_GRAY: 1,
    COLOR_RGB: 3,
    COLOR_PALETTE: 1,
    COLOR_GRAY_ALPHA: 2,
    COLOR_RGBA: 4,
}


def _read_chunks(data: bytes):
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            raise PngError(f"truncated {kind.decode('ascii', 'replace')} chunk")
        payload = data[start:end]
        expected = struct.unpack_from(">I", data, end)[0]
        actual = zlib.crc32(kind + payload) & 0xFFFFFFFF
        if actual != expected:
            raise PngError(f"bad CRC in {kind.decode('ascii', 'replace')} chunk")
        yield kind, payload
        offset = end + 4
    if offset != len(data):
        raise PngError("trailing bytes after final chunk")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0
    for row in range(height):
        if pos >= len(raw):
            raise PngError("truncated image data")
        filter_type = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        if len(line) != stride:
            raise PngError("truncated scanline")
        pos += 1 + stride

        if filter_type == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upper_left = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        elif filter_type != 0:
            raise PngError(f"unknown scanline filter {filter_type}")

        out[row * stride : (row + 1) * stride] = line
        previous = line
    return out


def _expand_bits(line: bytes, width: int, bit_depth: int) -> list[int]:
    values = []
    per_byte = 8 // bit_depth
    mask = (1 << bit_depth) - 1
    for i in range(width):
        byte = line[i // per_byte]
        shift = 8 - bit_depth * (i % per_byte + 1)
        values.append((byte >> shift) & mask)
    return values


def _scale_to_byte(value: int, bit_depth: int) -> int:
    if bit_depth == 8:
        return value
    maximum = (1 << bit_depth) - 1
    return (value * 255 + maximum // 2) // maximum


def decode_png(data: bytes) -> dict[str, Any]:
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG file")

    header = None
    palette = b""
    transparency = None
    idat = bytearray()
    for kind, payload in _read_chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break

    if header is None:
        raise PngError("missing IHDR")
    width, height, bit_depth, color_type, compression, filter_method, interlace = (
        header
    )
    if width == 0 or height == 0:
        raise PngError("zero sized image")
    if compression != 0 or filter_method != 0:
        raise PngError("unsupported compression or filter method")
    if interlace != 0:
        raise PngError("interlaced PNG (Adam7) is not supported")
    if color_type not in CHANNELS:
        raise PngError(f"unsupported color type {color_type}")
    if bit_depth not in (1, 2, 4, 8, 16):
        raise PngError(f"unsupported bit depth {bit_depth}")
    if bit_depth == 16 and color_type == COLOR_PALETTE:
        raise PngError("palette images cannot be 16 bit")
    if bit_depth < 8 and color_type not in (COLOR_GRAY, COLOR_PALETTE):
        raise PngError(
            f"bit depth {bit_depth} invalid for color type {color_type}"
        )
    if color_type == COLOR_PALETTE and not palette:
        raise PngError("palette image without PLTE")

    channels = CHANNELS[color_type]
    bits_per_pixel = channels * bit_depth
    stride = (width * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"corrupt image data: {exc}") from exc

    lines = _unfilter(raw, height, stride, bpp)
    pixels = bytearray(width * height * 4)

    for row in range(height):
        line = lines[row * stride : (row + 1) * stride]
        if bit_depth == 16:
            samples = list(
                struct.unpack(f">{width * channels}H", line[: width * channels * 2])
            )
            samples = [(s + 128) // 257 for s in samples]
        elif bit_depth == 8:
            samples = list(line[: width * channels])
        else:
            samples = _expand_bits(line, width, bit_depth)

        base = row * width * 4
        for x in range(width):
            out = base + x * 4
            if color_type == COLOR_PALETTE:
                index = samples[x]
                if index * 3 + 2 >= len(palette):
                    raise PngError(f"palette index {index} out of range")
                pixels[out] = palette[index * 3]
                pixels[out + 1] = palette[index * 3 + 1]
                pixels[out + 2] = palette[index * 3 + 2]
                pixels[out + 3] = (
                    transparency[index]
                    if transparency and index < len(transparency)
                    else 255
                )
                continue

            if color_type in (COLOR_GRAY, COLOR_GRAY_ALPHA):
                gray = samples[x * channels]
                if bit_depth < 8:
                    gray = _scale_to_byte(gray, bit_depth)
                pixels[out] = pixels[out + 1] = pixels[out + 2] = gray
                if color_type == COLOR_GRAY_ALPHA:
                    pixels[out + 3] = samples[x * channels + 1]
                else:
                    pixels[out + 3] = _gray_alpha(
                        samples[x], transparency, bit_depth
                    )
                continue

            red, green, blue = samples[x * channels : x * channels + 3]
            pixels[out] = red
            pixels[out + 1] = green
            pixels[out + 2] = blue
            if color_type == COLOR_RGBA:
                pixels[out + 3] = samples[x * channels + 3]
            else:
                pixels[out + 3] = _rgb_alpha(
                    samples[x * channels : x * channels + 3],
                    transparency,
                    bit_depth,
                )

    return {"width": width, "height": height, "pixels": bytes(pixels)}


def _gray_alpha(sample: int, transparency: bytes | None, bit_depth: int) -> int:
    if not transparency or len(transparency) < 2:
        return 255
    key = struct.unpack(">H", transparency[:2])[0]
    if bit_depth == 16:
        key = (key + 128) // 257
    return 0 if sample == key else 255


def _rgb_alpha(
    sample: list[int], transparency: bytes | None, bit_depth: int
) -> int:
    if not transparency or len(transparency) < 6:
        return 255
    keys = list(struct.unpack(">HHH", transparency[:6]))
    if bit_depth == 16:
        keys = [(k + 128) // 257 for k in keys]
    return 0 if list(sample) == keys else 255


def _chunk(tag: bytes, payload: bytes) -> bytes:
    body = tag + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def encode_png(pixels: bytes, width: int, height: int) -> bytes:
    stride = width * 4
    if len(pixels) != stride * height:
        raise PngError(f"expected {stride * height} bytes, got {len(pixels)}")
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        raw += pixels[row * stride:(row + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, COLOR_RGBA, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )
