from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
from typing import Any

from iosc.build.graph import (
    BuildLayout,
    Stage,
    fingerprint,
    read_output_manifest,
    relative_key,
    write_output_manifest,
)
from iosc.config.manifest import Manifest
from iosc.core import get_reporter, write_atomic
from iosc.core.errors import XcassetsError
from iosc.formats import car_writer, png, xcassets
from iosc.formats.plist import write_plist

STAGE_NAME = "assets"
CAR_NAME = "Assets.car"
PRODUCED_NAME = "assets-outputs.json"
CATALOG_SUFFIX = ".xcassets"

# suffixes ios uses to find loose image resources
SCALE_SUFFIX = {"1x": "", "2x": "@2x", "3x": "@3x"}
IDIOM_SUFFIX = {"universal": "", "iphone": "~iphone", "ipad": "~ipad"}

COPYABLE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".svg"}


@dataclass
class AssetResult:
    car_path: Path | None = None
    rendition_count: int = 0
    app_icons: list[str] = field(default_factory=list)
    loose_files: list[Path] = field(default_factory=list)
    lost: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plist_partial: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return bool(self.loose_files or self.lost)

    def summary(self) -> str:
        parts = [f"{self.rendition_count} renditions"]
        if self.loose_files:
            parts.append(f"{len(self.loose_files)} loose resources")
        if self.lost:
            parts.append(f"{len(self.lost)} dropped")
        return ", ".join(parts)


def find_catalogs(manifest: Manifest, project: Path) -> list[Path]:
    catalogs: set[Path] = set()
    for pattern in manifest.resources:
        for match in project.glob(pattern):
            if match.is_dir() and match.suffix.lower() == CATALOG_SUFFIX:
                catalogs.add(match)
    return sorted(catalogs)


def loose_resource_name(
    asset: str,
    scale: str = "1x",
    idiom: str = "universal",
    extension: str = ".png",
) -> str:
    base = asset.replace("/", "_")
    return f"{base}{SCALE_SUFFIX.get(scale, '')}{IDIOM_SUFFIX.get(idiom, '')}{extension}"


def _copy_loose(source: Path, dest_dir: Path, filename: str, result: AssetResult) -> Path | None:
    dest = dest_dir / filename
    if dest.exists():
        if dest.read_bytes() == source.read_bytes():
            return dest
        stem, extension = os.path.splitext(filename)
        for n in range(2, 100):
            candidate = dest_dir / f"{stem}-{n}{extension}"
            if not candidate.exists():
                result.warnings.append(
                    f"{filename} was already taken, wrote {candidate.name}"
                )
                dest = candidate
                break
        else:
            result.lost.append({"path": str(source), "reason": "no free loose filename"})
            return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    result.loose_files.append(dest)
    return dest


def _recover_container(item: dict[str, Any], dest_dir: Path, result: AssetResult) -> int:
    recovered = 0
    root = Path(item["path"])
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            extension = os.path.splitext(filename)[1].lower()
            if extension not in COPYABLE_EXTENSIONS:
                continue
            container = Path(dirpath).name
            stem, container_ext = os.path.splitext(container)
            asset = stem if container_ext == ".imageset" else os.path.splitext(filename)[0]
            source = Path(dirpath) / filename
            name = loose_resource_name(asset, extension=extension)
            if _copy_loose(source, dest_dir, name, result):
                recovered += 1
    return recovered


# an asset the writer cant encode falls back to a loose resource
# colors have no file to fall back to
def _recover(item: dict[str, Any], dest_dir: Path, result: AssetResult) -> None:
    kind = item.get("kind")
    path = Path(item.get("path", ""))

    if kind == "container" and path.is_dir():
        if _recover_container(item, dest_dir, result):
            result.warnings.append(
                f"{item['reason']}: recovered {item.get('asset', path)} as loose resources"
            )
        else:
            result.lost.append(item)
        return

    if path.is_file() and path.suffix.lower() in COPYABLE_EXTENSIONS:
        filename = loose_resource_name(
            item.get("asset") or path.stem,
            item.get("scale", "1x"),
            item.get("idiom", "universal"),
            path.suffix.lower(),
        )
        if _copy_loose(path, dest_dir, filename, result):
            result.warnings.append(f"{item['reason']}: wrote loose {filename}")
            return

    result.lost.append(item)


def _primary_icon(name: str, bases: list[str]) -> dict[str, Any]:
    return {
        "CFBundlePrimaryIcon": {
            "CFBundleIconName": name,
            "CFBundleIconFiles": bases,
        }
    }


# springboard resolves the home screen icon through CFBundleIconFiles,
# CFBundleIconName alone only feeds the car lookup
def build_plist_partial(
    app_icons: list[str], loose_icon_files: list[Path] | None = None
) -> dict[str, Any]:
    if not app_icons:
        return {}
    primary = app_icons[0]
    partial: dict[str, Any] = {"CFBundleIconName": primary}
    if not loose_icon_files:
        return partial

    bases = sorted({Path(f).stem.split("@")[0].split("~")[0] for f in loose_icon_files})
    iphone = [b for b in bases if "60x60" in b] or bases
    partial["CFBundleIcons"] = _primary_icon(primary, iphone)
    partial["CFBundleIcons~ipad"] = _primary_icon(primary, bases)
    partial["CFBundleIconFiles"] = iphone
    return partial


def _format_points(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# actool drops the home screen sizes beside Assets.car as loose pngs too
def _write_home_screen_icons(
    parsed_catalogs: list[dict[str, Any]],
    icon_name: str,
    dest_dir: Path,
    result: AssetResult,
) -> list[Path]:
    written: list[Path] = []
    for idiom, point_size, scale in xcassets.HOME_SCREEN_LOOSE_ICONS:
        match = next(
            (
                r
                for p in parsed_catalogs
                for r in p["renditions"]
                if r["name"] == icon_name
                and r.get("idiom") == idiom
                and r.get("point_size") == point_size
                and r["scale"] == scale
            ),
            None,
        )
        if match is None:
            continue
        points = _format_points(point_size)
        suffix = "" if idiom == "iphone" else IDIOM_SUFFIX.get(idiom, "")
        filename = (
            f"{icon_name}{points}x{points}"
            f"{SCALE_SUFFIX.get(f'{scale}x', '')}{suffix}.png"
        )
        dest = dest_dir / filename
        write_atomic(
            dest, png.encode_png(match["pixels"], match["width"], match["height"])
        )
        result.loose_files.append(dest)
        written.append(dest)
    return written


def compile_catalogs(
    catalogs: list[Path],
    resources_dir: Path,
    platform: str = "ios",
    platform_version: str = "17.0",
) -> AssetResult:
    result = AssetResult()
    resources_dir.mkdir(parents=True, exist_ok=True)
    builder = car_writer.CarBuilder(platform=platform, platform_version=platform_version)

    parsed_catalogs: list[dict[str, Any]] = []
    for catalog in catalogs:
        try:
            parsed = xcassets.parse_catalog(str(catalog))
            xcassets.add_renditions(builder, parsed)
        except XcassetsError as err:
            result.warnings.append(f"skipped catalog {catalog} with {err}")
            continue
        parsed_catalogs.append(parsed)
        result.app_icons.extend(parsed["app_icons"])

    for parsed in parsed_catalogs:
        for item in parsed["unsupported"]:
            _recover(item, resources_dir, result)

    result.rendition_count = sum(len(p["renditions"]) for p in parsed_catalogs)
    if result.rendition_count:
        result.car_path = resources_dir / CAR_NAME
        write_atomic(result.car_path, builder.build())
    else:
        result.warnings.append("no renditions compiled, no Assets.car written")

    compiled = {r["name"] for p in parsed_catalogs for r in p["renditions"]}
    icon_in_car = [n for n in result.app_icons if n in compiled]
    if icon_in_car:
        loose_icons = _write_home_screen_icons(
            parsed_catalogs, icon_in_car[0], resources_dir, result
        )
    else:
        loose_icons = [
            f
            for f in result.loose_files
            if any(f.name.startswith(n.replace("/", "_")) for n in result.app_icons)
        ]
    result.plist_partial = build_plist_partial(result.app_icons, loose_icons)
    return result


def catalog_inputs(catalogs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for catalog in catalogs:
        files.extend(p for p in sorted(catalog.rglob("*")) if p.is_file())
    return files


def assets_stage(
    manifest: Manifest,
    layout: BuildLayout,
    partial_path: Path,
    catalogs: list[Path] | None = None,
) -> Stage:
    resolved = catalogs if catalogs is not None else find_catalogs(manifest, layout.project)
    inputs = catalog_inputs(resolved)
    produced_path = layout.root / PRODUCED_NAME

    print_ = fingerprint(
        {
            "catalogs": [relative_key(c, layout.project) for c in resolved],
            "files": [relative_key(p, layout.project) for p in inputs],
            "deployment_target": manifest.deployment_target,
        }
    )

    def run() -> None:
        result = compile_catalogs(
            resolved, layout.resources, platform_version=manifest.deployment_target
        )
        write_atomic(partial_path, write_plist(result.plist_partial, binary=True))
        produced = list(result.loose_files)
        if result.car_path is not None:
            produced.append(result.car_path)
        write_output_manifest(produced_path, layout.project, produced)

        reporter = get_reporter()
        reporter.detail(result.summary())
        for warning in result.warnings:
            reporter.warn(warning)
        for item in result.lost:
            reporter.warn(f"dropped {item.get('asset', item.get('path'))}")

    outputs = [partial_path, produced_path]
    outputs.extend(read_output_manifest(produced_path, layout.project))

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
    )
