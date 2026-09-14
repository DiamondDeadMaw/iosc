import struct
from typing import Any

from iosc.core.errors import CarError
from iosc.formats.car import (
    BOM_MAGIC,
    CARHEADER_SIZE,
    CARHEADER_TAG,
    COLR_TAG,
    CSI_TAG,
    EXTENDED_METADATA_SIZE,
    EXTENDED_METADATA_TAG,
    KEYFORMAT_TAG,
    LAYOUT_COLOR,
    PIXFMT_ARGB,
    TREE_MAGIC,
)

BOM_HEADER_RESERVED = 512
BLOCK_ALIGNMENT = 8
BLOCK_TABLE_MIN_CAPACITY = 256
FREE_LIST_SIZE = 20

TREE_BLOCK_SIZE = 4096
TREE_NODE_HEADER_SIZE = 12
TREE_ENTRY_SIZE = 8
MAX_ENTRIES_PER_NODE = (TREE_BLOCK_SIZE - TREE_NODE_HEADER_SIZE) // TREE_ENTRY_SIZE

CELM_TAG_ON_DISK = 0x43454C4D
COMPRESSION_RAW = 0
COMPRESSION_LZFSE = 4
KCBC_MAGIC = 0x4342434B
KCBC_ROW_ALIGNMENT = 64
KCBC_CHUNK_ROWS = 64
BITMAP_ENCODING_COMPRESSED = 0x10

CSI_HEADER_SIZE = 184
CSI_VERSION = 1
COLORSPACE_SRGB = 1
LAYOUT_IMAGE = 12

FACET_ELEMENT = 85
FACET_PART = 181

ATTR_ELEMENT = 1
ATTR_PART = 2
ATTR_DIMENSION1 = 8
ATTR_DIMENSION2 = 9
ATTR_IDENTIFIER = 17
ATTR_STATE = 10
ATTR_PRESENTATION_STATE = 14
ATTR_SCALE = 12
ATTR_PREVIOUS_STATE = 19
ATTR_PREVIOUS_VALUE = 18
ATTR_APPEARANCE = 7
ATTR_IDIOM = 15
ATTR_DISPLAY_GAMUT = 24

DEFAULT_KEY_FORMAT = [
    ATTR_ELEMENT, ATTR_PART, ATTR_IDENTIFIER, ATTR_STATE,
    ATTR_PRESENTATION_STATE, ATTR_SCALE, ATTR_PREVIOUS_STATE, ATTR_PREVIOUS_VALUE,
    ATTR_APPEARANCE, ATTR_IDIOM, ATTR_DISPLAY_GAMUT,
    ATTR_DIMENSION1, ATTR_DIMENSION2,
]

COREUI_VERSION = 1008
STORAGE_VERSION = 17
SCHEMA_VERSION = 2
KEY_SEMANTICS = 1


def compare_keys(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    return 0


class _KeySort:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def __lt__(self, other: "_KeySort") -> bool:
        return compare_keys(self.key, other.key) < 0


class BomWriter:
    def __init__(self) -> None:
        self.blocks: list[bytes] = [b""]
        self.vars: dict[str, int] = {}

    def add_block(self, data: bytes) -> int:
        self.blocks.append(data)
        return len(self.blocks) - 1

    def reserve_block(self) -> int:
        self.blocks.append(b"")
        return len(self.blocks) - 1

    def set_block(self, index: int, data: bytes) -> None:
        self.blocks[index] = data

    def set_var(self, name: str, block_index: int) -> None:
        self.vars[name] = block_index

    def add_tree(
        self,
        entries: list[tuple[bytes, bytes]],
        schema_block: int = 0,
        block_size: int = TREE_BLOCK_SIZE,
    ) -> int:
        ordered = sorted(entries, key=lambda kv: _KeySort(kv[0]))
        for prev, cur in zip(ordered, ordered[1:]):
            if compare_keys(prev[0], cur[0]) == 0:
                raise CarError(f"duplicate tree key {prev[0]!r}")

        pairs = [(self.add_block(v), self.add_block(k)) for k, v in ordered]
        max_entries = (block_size - TREE_NODE_HEADER_SIZE) // TREE_ENTRY_SIZE
        chunks = [pairs[i:i + max_entries] for i in range(0, len(pairs), max_entries)] or [[]]

        leaf_ids = [self.reserve_block() for _ in chunks]
        for position, (leaf_id, chunk) in enumerate(zip(leaf_ids, chunks)):
            forward = leaf_ids[position + 1] if position + 1 < len(leaf_ids) else 0
            backward = leaf_ids[position - 1] if position else 0
            self.set_block(leaf_id, _pack_node(1, chunk, forward, backward, block_size))

        if len(leaf_ids) == 1:
            root = leaf_ids[0]
        else:
            branch = []
            for leaf_id, chunk in list(zip(leaf_ids, chunks))[:-1]:
                branch.append((leaf_id, chunk[-1][1]))
            root = self.add_block(
                _pack_node(0, branch, 0, 0, block_size, final_child=leaf_ids[-1])
            )

        tree = struct.pack(
            ">4sIIIIBII", TREE_MAGIC, 1, root, block_size, len(pairs), 0, schema_block, 0
        )
        return self.add_block(tree)

    def build(self) -> bytes:
        out = bytearray(BOM_HEADER_RESERVED)
        pointers = [(0, 0)]
        for data in self.blocks[1:]:
            _pad_to_alignment(out)
            pointers.append((len(out), len(data)))
            out += data

        _pad_to_alignment(out)
        vars_offset = len(out)
        out += struct.pack(">I", len(self.vars))
        for name, block_index in self.vars.items():
            encoded = name.encode("ascii")
            out += struct.pack(">IB", block_index, len(encoded)) + encoded
        vars_length = len(out) - vars_offset

        _pad_to_alignment(out)
        index_offset = len(out)
        capacity = _block_table_capacity(len(pointers))
        out += struct.pack(">I", capacity)
        for addr, length in pointers:
            out += struct.pack(">II", addr, length)
        out += b"\x00" * ((capacity - len(pointers)) * 8)
        out += b"\x00" * FREE_LIST_SIZE
        index_length = len(out) - index_offset

        struct.pack_into(
            ">8sIIIIII", out, 0, BOM_MAGIC, 1, len(self.blocks) - 1,
            index_offset, index_length, vars_offset, vars_length,
        )
        return bytes(out)


def _pad_to_alignment(buffer: bytearray) -> None:
    remainder = len(buffer) % BLOCK_ALIGNMENT
    if remainder:
        buffer += b"\x00" * (BLOCK_ALIGNMENT - remainder)


def _block_table_capacity(used_entries: int) -> int:
    if used_entries < BLOCK_TABLE_MIN_CAPACITY:
        return BLOCK_TABLE_MIN_CAPACITY
    return (used_entries + 8) & ~7


def _pack_node(
    is_leaf: int,
    entries: list[tuple[int, int]],
    forward: int,
    backward: int,
    block_size: int,
    final_child: int | None = None,
) -> bytes:
    node = bytearray(struct.pack(">HHII", is_leaf, len(entries), forward, backward))
    for left, right in entries:
        node += struct.pack(">II", left, right)
    if final_child is not None:
        node += struct.pack(">I", final_child)
    node += b"\x00" * (block_size - len(node))
    return bytes(node)


def build_carheader(rendition_count: int, version_string: str) -> bytes:
    block = bytearray(CARHEADER_SIZE)
    struct.pack_into(
        "<IIIII", block, 0, CARHEADER_TAG, COREUI_VERSION,
        STORAGE_VERSION, 0, rendition_count
    )
    main_version = b"@(#)PROGRAM:CoreUI  PROJECT:CoreUI-1008"
    block[0x14:0x14 + len(main_version)] = main_version
    encoded = version_string.encode("utf-8")[:255]
    block[0x94:0x94 + len(encoded)] = encoded
    struct.pack_into("<IIII", block, 0x1A4, 0, SCHEMA_VERSION, COLORSPACE_SRGB, KEY_SEMANTICS)
    return bytes(block)


def build_extended_metadata(platform: str, platform_version: str, authoring_tool: str) -> bytes:
    block = bytearray(EXTENDED_METADATA_SIZE)
    struct.pack_into("<I", block, 0, EXTENDED_METADATA_TAG)
    for offset, text in ((260, platform_version), (516, platform), (772, authoring_tool)):
        encoded = text.encode("utf-8")[:255]
        block[offset:offset + len(encoded)] = encoded
    return bytes(block)


def build_keyformat(tokens: list[int]) -> bytes:
    return struct.pack("<III", KEYFORMAT_TAG, 0, len(tokens)) + struct.pack(
        f"<{len(tokens)}I", *tokens
    )


def build_facet_value(identifier: int) -> bytes:
    attributes = [
        (ATTR_ELEMENT, FACET_ELEMENT),
        (ATTR_PART, FACET_PART),
        (ATTR_IDENTIFIER, identifier),
    ]
    block = struct.pack("<HHH", 0, 0, len(attributes))
    for attr_id, value in attributes:
        block += struct.pack("<HH", attr_id, value)
    return block


def build_rendition_key(key_format: list[int], values: dict[int, int]) -> bytes:
    return struct.pack(
        f"<{len(key_format)}H", *(values.get(token, 0) for token in key_format)
    )


def rgba_to_premultiplied_bgra(pixels: bytes) -> bytes:
    if len(pixels) % 4:
        raise CarError(f"RGBA buffer is not a multiple of 4 bytes: {len(pixels)}")
    out = bytearray(len(pixels))
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
        if a != 255:
            r = (r * a + 127) // 255
            g = (g * a + 127) // 255
            b = (b * a + 127) // 255
        out[i] = b
        out[i + 1] = g
        out[i + 2] = r
        out[i + 3] = a
    return bytes(out)


def build_raw_bitmap_payload(pixels: bytes) -> bytes:
    return struct.pack("<IIII", CELM_TAG_ON_DISK, 0, COMPRESSION_RAW, len(pixels)) + pixels


def _align_up(value: int, alignment: int) -> int:
    remainder = value % alignment
    return value + (alignment - remainder) if remainder else value


def build_kcbc_bitmap_payload(pixels: bytes, width: int, height: int, bpp: int = 4) -> bytes:
    from iosc.toolchain.lzfse import encode_lzfse_framed

    row_bytes = width * bpp
    aligned_row_bytes = _align_up(row_bytes, KCBC_ROW_ALIGNMENT)
    padding = b"\x00" * (aligned_row_bytes - row_bytes)

    chunks = bytearray()
    chunk_count = 0
    for start in range(0, height, KCBC_CHUNK_ROWS):
        chunk_height = min(KCBC_CHUNK_ROWS, height - start)
        rows = bytearray()
        for row in range(start, start + chunk_height):
            rows += pixels[row * row_bytes:(row + 1) * row_bytes]
            rows += padding
        compressed = encode_lzfse_framed(bytes(rows))
        chunks += struct.pack("<IIIII", KCBC_MAGIC, 0, 0, chunk_height, len(compressed))
        chunks += compressed
        chunk_count += 1

    return struct.pack(
        "<IIII", CELM_TAG_ON_DISK, 0, COMPRESSION_LZFSE, chunk_count
    ) + bytes(chunks)


def build_color_payload(components: tuple[float, ...], colorspace_id: int = COLORSPACE_SRGB) -> bytes:
    return struct.pack("<IIII", COLR_TAG, 1, colorspace_id, len(components)) + struct.pack(
        f"<{len(components)}d", *components
    )


def build_csi_rendition(
    name: str,
    width: int,
    height: int,
    scale: int,
    payload: bytes,
    pixfmt: int = PIXFMT_ARGB,
    layout: int = LAYOUT_IMAGE,
    flags: int = 0,
) -> bytes:
    header = bytearray(CSI_HEADER_SIZE)
    struct.pack_into(
        "<IIIIIIII", header, 0, CSI_TAG, CSI_VERSION, flags, width, height,
        scale * 100, pixfmt, COLORSPACE_SRGB
    )
    struct.pack_into("<IHH", header, 0x20, 0, layout, 0)
    encoded = name.encode("utf-8")[:127]
    header[0x28:0x28 + len(encoded)] = encoded
    struct.pack_into("<IIII", header, 0xA8, 0, 1, 0, len(payload))
    return bytes(header) + payload


class CarBuilder:
    def __init__(
        self,
        platform: str = "ios",
        platform_version: str = "17.0",
        authoring_tool: str = "iosc",
        key_format: list[int] | None = None,
    ) -> None:
        self.platform = platform
        self.platform_version = platform_version
        self.authoring_tool = authoring_tool
        self.key_format = list(key_format or DEFAULT_KEY_FORMAT)
        self.assets: dict[str, list[dict[str, Any]]] = {}
        self._next_identifier = 1

    def add_image(
        self,
        name: str,
        width: int,
        height: int,
        scale: int,
        pixels: bytes,
        pixel_order: str = "rgba",
        attributes: dict[int, int] | None = None,
    ) -> None:
        expected = width * height * 4
        if len(pixels) != expected:
            raise CarError(
                f"{name}@{scale}x: expected {expected} pixel bytes, got {len(pixels)}"
            )
        if pixel_order == "rgba":
            pixels = rgba_to_premultiplied_bgra(pixels)
        elif pixel_order != "bgra_premultiplied":
            raise CarError(f"unknown pixel_order {pixel_order!r}")
        self._append(name, {
            "kind": "image", "width": width, "height": height, "scale": scale,
            "pixels": pixels, "attributes": dict(attributes or {}),
        })

    def add_color(
        self,
        name: str,
        components: tuple[float, ...],
        attributes: dict[int, int] | None = None,
        colorspace_id: int = COLORSPACE_SRGB,
    ) -> None:
        if len(components) not in (2, 4):
            raise CarError(f"{name}: expected 2 or 4 color components, got {len(components)}")
        self._append(name, {
            "kind": "color", "components": tuple(float(c) for c in components),
            "colorspace_id": colorspace_id, "scale": 0,
            "attributes": dict(attributes or {}),
        })

    def _key_signature(self, rendition: dict[str, Any]) -> tuple[int, ...]:
        values = dict(rendition["attributes"])
        values[ATTR_SCALE] = rendition["scale"]
        return tuple(values.get(attr, 0) for attr in self.key_format
                     if attr != ATTR_IDENTIFIER)

    def _append(self, name: str, rendition: dict[str, Any]) -> None:
        renditions = self.assets.setdefault(name, [])
        signature = self._key_signature(rendition)
        for existing in renditions:
            if self._key_signature(existing) == signature:
                raise CarError(f"duplicate rendition for {name!r} with {signature}")
        renditions.append(rendition)

    def build(self) -> bytes:
        if not self.assets:
            raise CarError("no assets added")

        bom = BomWriter()
        rendition_count = sum(len(v) for v in self.assets.values())
        bom.set_var("CARHEADER", bom.add_block(
            build_carheader(rendition_count, f"iosc via {self.authoring_tool}")
        ))
        bom.set_var("EXTENDED_METADATA", bom.add_block(build_extended_metadata(
            self.platform, self.platform_version, self.authoring_tool
        )))
        keyformat_block = bom.add_block(build_keyformat(self.key_format))
        bom.set_var("KEYFORMAT", keyformat_block)

        facet_entries = []
        rendition_entries = []
        next_identifier = 1
        for name in sorted(self.assets):
            identifier = next_identifier
            next_identifier += 1
            facet_entries.append((name.encode("utf-8"), build_facet_value(identifier)))
            for rendition in self.assets[name]:
                values = {
                    ATTR_ELEMENT: FACET_ELEMENT,
                    ATTR_PART: FACET_PART,
                    ATTR_IDENTIFIER: identifier,
                    ATTR_SCALE: rendition["scale"],
                }
                values.update(rendition["attributes"])
                key = build_rendition_key(self.key_format, values)
                if rendition["kind"] == "color":
                    value = build_csi_rendition(
                        name, 0, 0, rendition["scale"],
                        build_color_payload(
                            rendition["components"], rendition["colorspace_id"]
                        ),
                        pixfmt=0, layout=LAYOUT_COLOR,
                    )
                else:
                    payload = build_kcbc_bitmap_payload(
                        rendition["pixels"], rendition["width"], rendition["height"]
                    )
                    value = build_csi_rendition(
                        name, rendition["width"], rendition["height"],
                        rendition["scale"], payload, flags=BITMAP_ENCODING_COMPRESSED,
                    )
                rendition_entries.append((key, value))

        bom.set_var("RENDITIONS", bom.add_tree(rendition_entries, keyformat_block))
        bom.set_var("FACETKEYS", bom.add_tree(facet_entries))
        return bom.build()
