import json
import os
from typing import Any

from iosc.core.errors import UnsupportedVariant, XcassetsError
from iosc.formats import car_writer, png

IDIOM_VALUES = {
    "universal": 0, "iphone": 1, "ipad": 2, "tv": 3, "car": 4,
    "watch": 5, "marketing": 6, "mac": 7, "vision": 8,
}

APPEARANCE_VALUES = {
    ("luminosity", "light"): 0,
    ("luminosity", "dark"): 1,
    ("luminosity", "tinted"): 10,
}

GAMUT_VALUES = {"srgb": 0, "display-p3": 1}
SCALE_VALUES = {"1x": 1, "2x": 2, "3x": 3}

RGBA_COMPONENTS = ("red", "green", "blue", "alpha")
GRAY_COMPONENTS = ("white", "alpha")

IMAGE_EXTENSIONS = {".png"}
SUPPORTED_CONTAINERS = {".imageset", ".colorset", ".appiconset"}

ICON_SIZE_SLOTS = {20: 1, 29: 2, 40: 3, 60: 4, 76: 5, 83.5: 6, 1024: 8}

APP_ICON_SLOTS = (
    ("iphone", 20, 2), ("iphone", 29, 1), ("iphone", 29, 2), ("iphone", 40, 2),
    ("iphone", 60, 2), ("iphone", 20, 3), ("iphone", 29, 3), ("iphone", 40, 3),
    ("iphone", 60, 3),
    ("ipad", 20, 1), ("ipad", 29, 1), ("ipad", 40, 1), ("ipad", 76, 1),
    ("ipad", 20, 2), ("ipad", 29, 2), ("ipad", 40, 2), ("ipad", 76, 2),
    ("ipad", 83.5, 2),
    ("marketing", 1024, 1),
)

HOME_SCREEN_LOOSE_ICONS = (("iphone", 60, 2), ("ipad", 76, 2))
KNOWN_UNSUPPORTED = {
    ".symbolset", ".dataset", ".iconset", ".icon", ".spriteatlas", ".textureset",
    ".mipmapset", ".stickerpack", ".solidimagestack", ".solidimagestacklayer",
    ".cubetextureset", ".brandassets", ".imagestack", ".complicationset",
}


def _load_contents(directory: str) -> dict[str, Any]:
    path = os.path.join(directory, "Contents.json")
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XcassetsError(f"{path} is not valid JSON: {exc}") from exc


def _parse_component(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16) / 255.0
    return float(text)


def _color_components(color: dict[str, Any]) -> tuple[str, tuple[float, ...]]:
    space = str(color.get("color-space", "srgb")).lower()
    if "reference" in color:
        raise UnsupportedVariant(
            f"color references the system color {color['reference']!r}"
        )
    components = color.get("components")
    if not isinstance(components, dict):
        raise XcassetsError(f"color has no components dictionary: {color!r}")
    names = GRAY_COMPONENTS if "white" in components else RGBA_COMPONENTS
    missing = [n for n in names if n not in components]
    if missing:
        raise XcassetsError(f"color is missing components {missing}")
    return space, tuple(_parse_component(components[n]) for n in names)


def _variant_attributes(item: dict[str, Any]) -> dict[int, int]:
    attributes: dict[int, int] = {}
    idiom = item.get("idiom", "universal")
    if idiom not in IDIOM_VALUES:
        raise XcassetsError(f"unknown idiom {idiom!r}")
    attributes[car_writer.ATTR_IDIOM] = IDIOM_VALUES[idiom]

    appearances = item.get("appearances", [])
    pairs = [(a.get("appearance"), a.get("value")) for a in appearances]
    if len(pairs) > 1:
        raise UnsupportedVariant(
            f"combined appearances {pairs} need a per-catalog APPEARANCEKEYS table"
        )
    for pair in pairs:
        if pair[0] == "contrast":
            raise UnsupportedVariant(f"contrast appearance {pair[1]!r} is not mapped yet")
        if pair not in APPEARANCE_VALUES:
            raise XcassetsError(f"unknown appearance {pair}")
        attributes[car_writer.ATTR_APPEARANCE] = APPEARANCE_VALUES[pair]

    gamut = item.get("display-gamut")
    if gamut is not None:
        if gamut.lower() not in GAMUT_VALUES:
            raise XcassetsError(f"unknown display gamut {gamut!r}")
        attributes[car_writer.ATTR_DISPLAY_GAMUT] = GAMUT_VALUES[gamut.lower()]
    return attributes


def _parse_imageset(
    directory: str,
    name: str,
    contents: dict[str, Any],
    unsupported: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    renditions: list[dict[str, Any]] = []
    for item in contents.get("images", []):
        filename = item.get("filename")
        if not filename:
            continue
        extension = os.path.splitext(filename)[1].lower()
        if extension not in IMAGE_EXTENSIONS:
            unsupported.append({
                "path": os.path.join(directory, filename),
                "reason": f"{extension or 'no extension'} images are not supported yet",
                "kind": "file", "asset": name,
                "scale": item.get("scale", "1x"),
                "idiom": item.get("idiom", "universal"),
            })
            continue
        source = os.path.join(directory, filename)
        if not os.path.exists(source):
            raise XcassetsError(f"{name}: missing image file {source}")
        with open(source, "rb") as f:
            image = png.decode_png(f.read())

        scale_text = item.get("scale", "1x")
        if scale_text not in SCALE_VALUES:
            raise XcassetsError(f"{name}: unknown scale {scale_text!r}")

        try:
            attributes = _variant_attributes(item)
        except UnsupportedVariant as exc:
            unsupported.append({
                "path": source, "reason": str(exc), "kind": "variant",
                "asset": name, "scale": scale_text,
                "idiom": item.get("idiom", "universal"),
            })
            continue

        renditions.append({
            "name": name, "kind": "image", "source": source,
            "width": image["width"], "height": image["height"],
            "scale": SCALE_VALUES[scale_text], "pixels": image["pixels"],
            "attributes": attributes,
        })
    return renditions


def _resize_rgba(pixels: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    out = bytearray(dst_w * dst_h * 4)
    x_ratio = src_w / dst_w
    y_ratio = src_h / dst_h
    for dy in range(dst_h):
        sy = (dy + 0.5) * y_ratio - 0.5
        sy0 = max(0, min(src_h - 1, int(sy)))
        sy1 = min(src_h - 1, sy0 + 1)
        fy = min(1.0, max(0.0, sy - sy0))
        for dx in range(dst_w):
            sx = (dx + 0.5) * x_ratio - 0.5
            sx0 = max(0, min(src_w - 1, int(sx)))
            sx1 = min(src_w - 1, sx0 + 1)
            fx = min(1.0, max(0.0, sx - sx0))
            dst_index = (dy * dst_w + dx) * 4
            i00 = (sy0 * src_w + sx0) * 4
            i10 = (sy0 * src_w + sx1) * 4
            i01 = (sy1 * src_w + sx0) * 4
            i11 = (sy1 * src_w + sx1) * 4
            for c in range(4):
                top = pixels[i00 + c] + (pixels[i10 + c] - pixels[i00 + c]) * fx
                bottom = pixels[i01 + c] + (pixels[i11 + c] - pixels[i01 + c]) * fx
                out[dst_index + c] = max(0, min(255, round(top + (bottom - top) * fy)))
    return bytes(out)


def _synthesize_legacy_icon_sizes(
    name: str, renditions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(renditions) != 1:
        return []
    source = renditions[0]
    if source["kind"] != "image":
        return []
    if source["attributes"].get(car_writer.ATTR_IDIOM, IDIOM_VALUES["universal"]) != IDIOM_VALUES["universal"]:
        return []
    if source["scale"] != 1 or source["width"] != source["height"] or source["width"] < 512:
        return []

    synthesized = []
    for idiom, point_size, scale in APP_ICON_SLOTS:
        pixel_size = round(point_size * scale)
        pixels = _resize_rgba(
            source["pixels"], source["width"], source["height"],
            pixel_size, pixel_size,
        )
        synthesized.append({
            "name": name, "kind": "image", "source": source["source"],
            "width": pixel_size, "height": pixel_size, "scale": scale,
            "point_size": point_size, "idiom": idiom,
            "pixels": pixels,
            "attributes": {
                car_writer.ATTR_IDIOM: IDIOM_VALUES[idiom],
                car_writer.ATTR_DIMENSION2: ICON_SIZE_SLOTS[point_size],
            },
        })
    return synthesized


def _parse_colorset(
    directory: str,
    name: str,
    contents: dict[str, Any],
    unsupported: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    renditions: list[dict[str, Any]] = []
    for item in contents.get("colors", []):
        color = item.get("color")
        if not isinstance(color, dict):
            continue
        try:
            space, components = _color_components(color)
            attributes = _variant_attributes(item)
        except UnsupportedVariant as exc:
            unsupported.append({
                "path": directory, "reason": str(exc), "kind": "variant",
                "asset": name, "idiom": item.get("idiom", "universal"),
            })
            continue
        renditions.append({
            "name": name, "kind": "color", "color_space": space,
            "components": components, "scale": 0,
            "attributes": attributes,
        })
    return renditions


def parse_catalog(path: str) -> dict[str, Any]:
    if not os.path.isdir(path):
        raise XcassetsError(f"not a directory: {path}")

    renditions: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    app_icons: list[str] = []

    def walk(directory: str, prefix: str) -> None:
        for entry in sorted(os.listdir(directory)):
            full = os.path.join(directory, entry)
            if not os.path.isdir(full):
                continue
            stem, extension = os.path.splitext(entry)
            extension = extension.lower()

            if extension in KNOWN_UNSUPPORTED:
                unsupported.append({
                    "path": full, "kind": "container", "asset": prefix + stem,
                    "reason": f"{extension} is not supported yet",
                })
                continue

            if extension in SUPPORTED_CONTAINERS:
                contents = _load_contents(full)
                name = prefix + stem
                if extension == ".colorset":
                    renditions.extend(
                        _parse_colorset(full, name, contents, unsupported))
                elif extension == ".appiconset":
                    app_icons.append(name)
                    icon_renditions = _parse_imageset(full, name, contents, unsupported)
                    renditions.extend(icon_renditions)
                    renditions.extend(
                        _synthesize_legacy_icon_sizes(name, icon_renditions))
                else:
                    renditions.extend(_parse_imageset(full, name, contents, unsupported))
                continue

            if extension:
                unsupported.append({
                    "path": full, "kind": "container", "asset": prefix + stem,
                    "reason": f"unrecognized container {extension}",
                })
                continue

            contents = _load_contents(full)
            provides = contents.get("properties", {}).get("provides-namespace", False)
            walk(full, f"{prefix}{entry}/" if provides else prefix)

    walk(path, "")
    return {"renditions": renditions, "unsupported": unsupported,
            "app_icons": app_icons}


def add_renditions(builder: car_writer.CarBuilder, parsed: dict[str, Any]) -> None:
    kept = []
    for rendition in parsed["renditions"]:
        try:
            if rendition["kind"] == "image":
                builder.add_image(
                    rendition["name"], rendition["width"], rendition["height"],
                    rendition["scale"], rendition["pixels"],
                    attributes=rendition["attributes"],
                )
            else:
                builder.add_color(
                    rendition["name"], rendition["components"],
                    attributes=rendition["attributes"],
                )
        except car_writer.CarError as exc:
            parsed["unsupported"].append({
                "path": rendition.get("source", rendition["name"]),
                "reason": str(exc), "kind": "variant",
                "asset": rendition["name"],
            })
            continue
        kept.append(rendition)
    parsed["renditions"] = kept


def build_car(
    catalog_path: str,
    output_path: str,
    platform: str = "ios",
    platform_version: str = "17.0",
) -> dict[str, Any]:
    parsed = parse_catalog(catalog_path)
    builder = car_writer.CarBuilder(platform=platform, platform_version=platform_version)
    add_renditions(builder, parsed)

    with open(output_path, "wb") as f:
        f.write(builder.build())
    return parsed
