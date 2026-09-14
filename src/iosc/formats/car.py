from collections.abc import Callable
from dataclasses import dataclass
import struct
from typing import Any

from iosc.core.errors import CarError

BOM_MAGIC = b"BOMStore"
CARHEADER_TAG = 0x43544152
CARHEADER_SIZE = 436
EXTENDED_METADATA_TAG = 0x4154454D
EXTENDED_METADATA_SIZE = 1028
KEYFORMAT_TAG = 0x6B666D74
TREE_MAGIC = b"tree"

ATTRIBUTE_NAMES = {
    0: "ThemeLook",
    1: "Element",
    2: "Part",
    3: "Size",
    4: "Direction",
    5: "Placeholder",
    6: "Value",
    7: "ThemeAppearance",
    8: "Dimension1",
    9: "Dimension2",
    10: "State",
    11: "Layer",
    12: "Scale",
    13: "Unknown13",
    14: "PresentationState",
    15: "Idiom",
    16: "Subtype",
    17: "Identifier",
    18: "PreviousValue",
    19: "PreviousState",
    20: "HorizontalSizeClass",
    21: "VerticalSizeClass",
    22: "MemoryLevelClass",
    23: "GraphicsFeatureSetClass",
    24: "DisplayGamut",
    25: "DeploymentTarget",
}

CSI_TAG = 0x43545349
RAWD_TAG = 0x52415744
COLR_TAG = 0x434F4C52
CELM_TAG_LE = 0x4D4C4543
CELM_TAG_BE = 0x43454C4D

PIXFMT_PDF = 0x50444620
PIXFMT_SVG = 0x53564720
PIXFMT_ARGB = 0x41524742
PIXFMT_GA8 = 0x47413820
PIXFMT_GA16 = 0x47413136
PIXFMT_RGBW = 0x52474257
PIXFMT_RASTER = {PIXFMT_ARGB, PIXFMT_GA8, PIXFMT_GA16, PIXFMT_RGBW}
PIXFMT_BPP = {PIXFMT_ARGB: 4, PIXFMT_GA8: 2, PIXFMT_GA16: 4}

LAYOUT_TEXT_EFFECT = 7
LAYOUT_INTERNAL_LINK = 1003
LAYOUT_COLOR = 1009

KCBC_MAGIC = 0x4342434B
DMP2_TAG = 0x32706D64


@dataclass(frozen=True)
class Decompressors:
    lzfse_framed: Callable[[bytes], bytes]
    lzvn_raw: Callable[[bytes, int], bytes]


class BomFile:
    def __init__(self, data: bytes) -> None:
        self.data = data
        if data[0:8] != BOM_MAGIC:
            raise CarError(f"not a BOM file, magic={data[0:8]!r}")
        (
            version,
            num_blocks,
            index_offset,
            index_length,
            vars_offset,
            vars_length,
        ) = struct.unpack_from(">IIIIII", data, 8)
        self.version = version
        self.number_of_blocks = num_blocks
        self.pointers = self._read_block_table(index_offset)
        self.vars = self._read_vars(vars_offset)

    def _read_block_table(self, index_offset: int) -> list[tuple[int, int]]:
        cap = struct.unpack_from(">I", self.data, index_offset)[0]
        pointers = []
        off = index_offset + 4
        for i in range(cap):
            addr, length = struct.unpack_from(">II", self.data, off + i * 8)
            pointers.append((addr, length))
        return pointers

    def _read_vars(self, vars_offset: int) -> dict[str, int]:
        count = struct.unpack_from(">I", self.data, vars_offset)[0]
        off = vars_offset + 4
        result = {}
        for _ in range(count):
            block_index = struct.unpack_from(">I", self.data, off)[0]
            name_len = self.data[off + 4]
            name = self.data[off + 5 : off + 5 + name_len].decode("ascii")
            result[name] = block_index
            off += 5 + name_len
        return result

    def get_block(self, block_index: int) -> bytes:
        addr, length = self.pointers[block_index]
        return self.data[addr : addr + length]


def parse_carheader(block: bytes) -> dict[str, Any]:
    if len(block) != CARHEADER_SIZE:
        raise CarError(f"CARHEADER wrong size: {len(block)}")
    (
        tag,
        coreui_version,
        storage_version,
        storage_timestamp,
        rendition_count,
    ) = struct.unpack_from("<IIIII", block, 0)
    if tag != CARHEADER_TAG:
        raise CarError(f"CARHEADER wrong tag: {tag:#x}")
    main_version = (
        block[0x14 : 0x14 + 128].split(b"\x00", 1)[0].decode("utf-8", "replace")
    )
    version_string = (
        block[0x94 : 0x94 + 256].split(b"\x00", 1)[0].decode("utf-8", "replace")
    )
    uuid = block[0x194:0x1A4]
    associated_checksum, schema_version, colorspace_id, key_semantics = (
        struct.unpack_from("<IIII", block, 0x1A4)
    )
    return {
        "coreui_version": coreui_version,
        "storage_version": storage_version,
        "storage_timestamp": storage_timestamp,
        "rendition_count": rendition_count,
        "main_version_string": main_version,
        "version_string": version_string,
        "uuid": uuid.hex(),
        "associated_checksum": associated_checksum,
        "schema_version": schema_version,
        "colorspace_id": colorspace_id,
        "key_semantics": key_semantics,
    }


def parse_extended_metadata(block: bytes) -> dict[str, Any]:
    if len(block) != EXTENDED_METADATA_SIZE:
        raise CarError(f"EXTENDED_METADATA wrong size: {len(block)}")
    tag = struct.unpack_from("<I", block, 0)[0]
    if tag != EXTENDED_METADATA_TAG:
        raise CarError(f"EXTENDED_METADATA wrong tag: {tag:#x}")

    def field(offset):
        return (
            block[offset : offset + 256]
            .split(b"\x00", 1)[0]
            .decode("utf-8", "replace")
        )

    return {
        "thinning_arguments": field(4),
        "deployment_platform_version": field(260),
        "deployment_platform": field(516),
        "authoring_tool": field(772),
    }


def parse_keyformat(block: bytes) -> list[int]:
    tag, version, token_count = struct.unpack_from("<III", block, 0)
    if tag != KEYFORMAT_TAG:
        raise CarError(f"KEYFORMAT wrong tag: {tag:#x}")
    return list(struct.unpack_from(f"<{token_count}I", block, 12))


def extract_tree_entries(
    bom: BomFile, tree_block_index: int
) -> list[tuple[int, int]]:
    tree_data = bom.get_block(tree_block_index)
    magic = tree_data[0:4]
    if magic != TREE_MAGIC:
        raise CarError(f"not a BOM tree, magic={magic!r}")
    root_idx = struct.unpack_from(">I", tree_data, 8)[0]

    curr = root_idx
    while True:
        node = bom.get_block(curr)
        is_leaf = struct.unpack_from(">H", node, 0)[0]
        if is_leaf == 1:
            break
        curr = struct.unpack_from(">I", node, 12)[0]

    entries = []
    n = curr
    while n != 0:
        node = bom.get_block(n)
        is_leaf, count, forward, backward = struct.unpack_from(">HHII", node, 0)
        if is_leaf != 1:
            raise CarError(f"corrupt tree, expected leaf at block {n}")
        for i in range(count):
            val_idx, key_idx = struct.unpack_from(">II", node, 12 + i * 8)
            entries.append((val_idx, key_idx))
        n = forward
    return entries


def parse_facet_value(block: bytes) -> dict[str, Any]:
    hotspot_x, hotspot_y, num_attrs = struct.unpack_from("<HHH", block, 0)
    attrs = {}
    for i in range(num_attrs):
        off = 6 + i * 4
        attr_id, attr_val = struct.unpack_from("<HH", block, off)
        attrs[ATTRIBUTE_NAMES.get(attr_id, f"Attr{attr_id}")] = attr_val
    return {"hotspot": (hotspot_x, hotspot_y), "attributes": attrs}


def parse_rendition_key(
    key_block: bytes, key_format: list[int]
) -> dict[str, Any]:
    values = struct.unpack_from(f"<{len(key_format)}H", key_block, 0)
    return {
        ATTRIBUTE_NAMES.get(attr_id, f"Attr{attr_id}"): val
        for attr_id, val in zip(key_format, values)
    }


def parse_csiheader(block: bytes) -> dict[str, Any]:
    if len(block) < 184:
        raise CarError(
            f"rendition value block too short for csiheader: {len(block)}"
        )
    (
        tag,
        version,
        flags,
        width,
        height,
        scale,
        pixfmt,
        colorspace,
    ) = struct.unpack_from("<IIIIIIII", block, 0)
    if tag != CSI_TAG:
        raise CarError(f"bad csiheader tag {tag:#x}")
    modtime, layout, zero = struct.unpack_from("<IHH", block, 0x20)
    name = (
        block[0x28 : 0x28 + 128].split(b"\x00", 1)[0].decode("utf-8", "replace")
    )
    (
        tvl_length,
        bitmap_count,
        reserved,
        rendition_length,
    ) = struct.unpack_from("<IIII", block, 0xA8)
    return {
        "flags": flags,
        "width": width,
        "height": height,
        "scale": scale,
        "pixfmt": pixfmt,
        "colorspace_id": colorspace & 0xF,
        "layout": layout,
        "name": name,
        "tvl_length": tvl_length,
        "bitmap_count": bitmap_count,
        "rendition_length": rendition_length,
    }


def rendition_payload_offset(header: dict[str, Any]) -> int:
    return 184 + header["tvl_length"]


def decode_pdf_payload(payload: bytes) -> bytes:
    tag, version, raw_len = struct.unpack_from("<III", payload, 0)
    if tag != RAWD_TAG:
        raise CarError(f"bad RAWD tag {tag:#x}")
    if version != 0:
        raise CarError(f"PDF payload unexpectedly compressed (version={version})")
    return payload[12 : 12 + raw_len]


def decode_color_payload(payload: bytes) -> dict[str, Any]:
    tag, version, colorspace_id, num_components = struct.unpack_from(
        "<IIII", payload, 0
    )
    if tag != COLR_TAG:
        raise CarError(f"bad COLR tag {tag:#x}")
    components = struct.unpack_from(f"<{num_components}d", payload, 16)
    return {"colorspace_id": colorspace_id, "components": components}


def decode_kcbc_bitmap(
    payload: bytes, width: int, bpp: int, decompressors: Decompressors
) -> bytes:
    tag, flags, ctype, chunk_count = struct.unpack_from("<IIII", payload, 0)
    if ctype != 4:
        raise CarError(f"not a KCBC payload, compressionType={ctype}")
    offset = 16
    target_row_bytes = width * bpp
    rows = []
    for _ in range(chunk_count):
        (
            magic,
            res0,
            res1,
            chunk_h,
            comp_size,
        ) = struct.unpack_from("<IIIII", payload, offset)
        if magic != KCBC_MAGIC:
            raise CarError(f"corrupt KCBC chunk magic {magic:#x}")
        stream = payload[offset + 20 : offset + 20 + comp_size]
        decompressed = decompressors.lzfse_framed(stream)
        chunk_row_bytes = len(decompressed) // chunk_h
        for r in range(chunk_h):
            rows.append(
                decompressed[
                    r * chunk_row_bytes : r * chunk_row_bytes + target_row_bytes
                ]
            )
        offset += 20 + comp_size
    return b"".join(rows)


def decode_deepmap2_bitmap(
    payload: bytes,
    csi_width: int,
    csi_height: int,
    decompressors: Decompressors,
) -> bytes:
    celm_tag, flags, ctype, raw_len = struct.unpack_from("<IIII", payload, 0)
    if ctype != 11:
        raise CarError(f"not a Deepmap2 payload, compressionType={ctype}")
    dmp2_hdr = payload[32:48]
    magic = struct.unpack_from("<I", dmp2_hdr, 0)[0]
    if magic != DMP2_TAG:
        raise CarError(f"bad dmp2 magic {magic:#x}")
    width, height = struct.unpack_from("<HH", dmp2_hdr, 8)
    count_field, bytes_per_color = struct.unpack_from("<HH", dmp2_hdr, 12)
    stream_start = payload[48:]

    if bytes_per_color == 0:
        stream = stream_start[:count_field]
        return decompressors.lzfse_framed(stream)

    palette_count = count_field
    palette = stream_start[: palette_count * bytes_per_color]
    remaining = stream_start[palette_count * bytes_per_color :]
    index_len = struct.unpack_from("<I", remaining, 0)[0]
    index_stream = remaining[4 : 4 + index_len]
    indices = decompressors.lzvn_raw(index_stream, width * height)
    if len(indices) != width * height:
        raise CarError(
            f"decoded index count {len(indices)} != {width}x{height}={width * height}"
        )
    pixels = bytearray(width * height * bytes_per_color)
    for i, idx in enumerate(indices):
        color = palette[
            idx * bytes_per_color : idx * bytes_per_color + bytes_per_color
        ]
        pixels[i * bytes_per_color : i * bytes_per_color + bytes_per_color] = color
    return bytes(pixels)


def decode_raw_bitmap(
    payload: bytes, width: int, height: int, bpp: int
) -> bytes:
    raw_length = struct.unpack_from("<I", payload, 12)[0]
    pixels = payload[16 : 16 + raw_length]
    if len(pixels) != raw_length:
        raise CarError(
            f"truncated raw bitmap: {len(pixels)} of {raw_length} bytes"
        )
    expected = width * height * bpp
    if raw_length != expected:
        raise CarError(f"raw bitmap is {raw_length} bytes, expected {expected}")
    return pixels


def decode_bitmap_payload(
    payload: bytes,
    header: dict[str, Any],
    decompressors: Decompressors | None = None,
) -> bytes:
    if header["pixfmt"] not in PIXFMT_BPP:
        raise NotImplementedError(
            f"unverified raster pixel format {header['pixfmt']:#x}"
        )
    bpp = PIXFMT_BPP[header["pixfmt"]]
    ctype = struct.unpack_from("<I", payload, 8)[0]
    if ctype == 0:
        return decode_raw_bitmap(
            payload, header["width"], header["height"], bpp
        )
    if decompressors is None:
        raise CarError("Decompressors required for compressed rendition payload")
    if ctype == 4:
        return decode_kcbc_bitmap(
            payload, header["width"], bpp, decompressors
        )
    if ctype == 11:
        return decode_deepmap2_bitmap(
            payload, header["width"], header["height"], decompressors
        )
    raise NotImplementedError(
        f"compressionType {ctype} not observed in Xcode 27 corpus, decoder not implemented"
    )


def decode_svg_payload(
    payload: bytes, decompressors: Decompressors | None = None
) -> str:
    tag, version = struct.unpack_from("<II", payload, 0)
    if tag != RAWD_TAG:
        raise CarError(f"bad RAWD tag {tag:#x}")
    raw_len = struct.unpack_from("<I", payload, 8)[0]
    stream = payload[12 : 12 + raw_len]
    if version == 0:
        return stream.decode("utf-8")
    if decompressors is None:
        raise CarError("Decompressors required for compressed SVG payload")
    decoded = decompressors.lzfse_framed(stream)
    return decoded.decode("utf-8")


def decode_rendition_payload(
    value_block: bytes, decompressors: Decompressors | None = None
) -> bytes | str | dict[str, Any]:
    header = parse_csiheader(value_block)
    payload = value_block[rendition_payload_offset(header) :]

    if header["layout"] == LAYOUT_INTERNAL_LINK:
        raise NotImplementedError(
            "internal link rendition (deduplicated asset), resolving via TLV 1010 "
            "not yet implemented"
        )
    if header["layout"] == LAYOUT_COLOR:
        return decode_color_payload(payload)
    if header["pixfmt"] == PIXFMT_PDF:
        return decode_pdf_payload(payload)
    if header["pixfmt"] == PIXFMT_SVG:
        return decode_svg_payload(payload, decompressors)
    if header["pixfmt"] in PIXFMT_RASTER:
        return decode_bitmap_payload(payload, header, decompressors)
    raise NotImplementedError(f"unhandled pixel format {header['pixfmt']:#x}")


class AssetCatalog:
    def __init__(
        self, path: str, decompressors: Decompressors | None = None
    ) -> None:
        with open(path, "rb") as f:
            data = f.read()
        self.decompressors = decompressors
        self.bom = BomFile(data)
        self.header = parse_carheader(
            self.bom.get_block(self.bom.vars["CARHEADER"])
        )
        self.metadata = parse_extended_metadata(
            self.bom.get_block(self.bom.vars["EXTENDED_METADATA"])
        )
        self.key_format = parse_keyformat(
            self.bom.get_block(self.bom.vars["KEYFORMAT"])
        )

    def list_assets(self) -> list[dict[str, Any]]:
        entries = extract_tree_entries(self.bom, self.bom.vars["FACETKEYS"])
        assets = []
        for val_idx, key_idx in entries:
            name = self.bom.get_block(key_idx).decode("utf-8", "replace")
            facet = parse_facet_value(self.bom.get_block(val_idx))
            assets.append({"name": name, **facet})
        return assets

    def list_renditions(self) -> list[dict[str, Any]]:
        entries = extract_tree_entries(self.bom, self.bom.vars["RENDITIONS"])
        renditions = []
        for val_idx, key_idx in entries:
            key = parse_rendition_key(
                self.bom.get_block(key_idx), self.key_format
            )
            renditions.append({"key": key, "value_block_index": val_idx})
        return renditions

    def find_renditions_for_asset(self, asset_name: str) -> list[dict[str, Any]]:
        assets = self.list_assets()
        target = next((a for a in assets if a["name"] == asset_name), None)
        if target is None:
            raise CarError(f"no asset named {asset_name!r}")
        element = target["attributes"].get("Element")
        part = target["attributes"].get("Part")
        identifier = target["attributes"].get("Identifier")

        matches = []
        for r in self.list_renditions():
            key = r["key"]
            if (
                key.get("Element") == element
                and key.get("Part") == part
                and key.get("Identifier") == identifier
            ):
                matches.append(r)
        return matches

    def get_rendition_image(
        self,
        asset_name: str,
        scale: int = 2,
        decompressors: Decompressors | None = None,
    ) -> bytes | str | dict[str, Any]:
        candidates = self.find_renditions_for_asset(asset_name)
        match = next(
            (r for r in candidates if r["key"].get("Scale") == scale), None
        )
        if match is None:
            raise CarError(f"no rendition for {asset_name!r} at scale {scale}")
        value_block = self.bom.get_block(match["value_block_index"])
        active = decompressors or self.decompressors
        return decode_rendition_payload(value_block, active)
