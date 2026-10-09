from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from typing import Any

from iosc.build import assets
from iosc.core import PackageError, ensure_dir, get_reporter, rmtree_force
from iosc.formats import storyboard, strings, xib
from iosc.formats.plist import write_plist

BUNDLE_SUFFIX = ".bundle"
ACCESSOR_NAME = "resource_bundle_accessor.swift"
EMBEDDED_NAME = "embedded_resources.swift"
LPROJ_SUFFIX = ".lproj"

PROCESS = "process"
COPY = "copy"
EMBED = "embed"

# compilers iosc has no codec for yet
UNSUPPORTED_SUFFIXES = {
    ".metal": "Metal shaders",
    ".xcdatamodel": "Core Data models",
    ".xcdatamodeld": "Core Data models",
    ".xcmappingmodel": "Core Data mapping models",
    ".mlmodel": "Core ML models",
    ".mlpackage": "Core ML models",
    ".rcproject": "Reality Composer projects",
}

RE_BUNDLE_ID_UNSAFE = re.compile(r"[^A-Za-z0-9.-]+")


@dataclass(frozen=True)
class PackageResource:
    path: Path
    rule: str
    localization: str | None = None


def parse_rule(entry: dict[str, Any]) -> PackageResource:
    path = entry.get("path")
    rule = entry.get("rule")
    if not isinstance(path, str) or not isinstance(rule, dict) or len(rule) != 1:
        raise PackageError(f"swift package described a resource iosc cannot read, {entry}")
    kind, payload = next(iter(rule.items()))
    if kind == "process":
        localization = payload.get("localization") if isinstance(payload, dict) else None
        return PackageResource(Path(path), PROCESS, localization)
    if kind == "copy":
        return PackageResource(Path(path), COPY)
    if kind == "embed_in_code":
        return PackageResource(Path(path), EMBED)
    raise PackageError(f"swift package described a resource rule iosc does not know, {kind}")


def unsupported_reason(resource: PackageResource) -> str | None:
    if resource.rule != PROCESS:
        return None
    kind = UNSUPPORTED_SUFFIXES.get(resource.path.suffix.lower())
    if kind is None:
        return None
    return f"has a {resource.path.name} resource, iosc cannot compile {kind} yet"


def _lproj(resource: PackageResource) -> str:
    return f"{resource.localization}{LPROJ_SUFFIX}/" if resource.localization else ""


# bundle path, None when merged or expanded at build
def destination(resource: PackageResource) -> str | None:
    name = resource.path.name
    suffix = resource.path.suffix.lower()
    if resource.rule == EMBED:
        return None
    if resource.rule == COPY:
        return name
    if suffix in (assets.CATALOG_SUFFIX, ".xcstrings"):
        return None
    if suffix in (".strings", ".stringsdict"):
        return f"{_lproj(resource)}{resource.path.stem}{suffix}"
    if suffix == ".xib":
        return f"{_lproj(resource)}{resource.path.stem}.nib"
    if suffix == ".storyboard":
        return f"{_lproj(resource)}{resource.path.stem}.storyboardc"
    return f"{_lproj(resource)}{name}"


def duplicate_destination(resources: Sequence[PackageResource]) -> str | None:
    seen: set[str] = set()
    for resource in resources:
        landing = destination(resource)
        if landing is None:
            continue
        key = landing.lower()
        if key in seen:
            return landing
        seen.add(key)
    return None


def bundled(resources: Sequence[PackageResource]) -> list[PackageResource]:
    return [r for r in resources if r.rule != EMBED]


def embedded(resources: Sequence[PackageResource]) -> list[PackageResource]:
    return [r for r in resources if r.rule == EMBED]


def bundle_name(package_name: str, target: str) -> str:
    return f"{package_name}_{target}{BUNDLE_SUFFIX}"


def swift_literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# static link, bundle always in app root
def accessor_source(name: str) -> str:
    return "\n".join(
        [
            "import Foundation",
            "",
            "extension Foundation.Bundle {",
            "    static let module: Bundle = {",
            f"        let path = Bundle.main.bundleURL.appendingPathComponent({swift_literal(name)}).path",
            "        guard let bundle = Bundle(path: path) else {",
            '            Swift.fatalError("could not load resource bundle from \\(path)")',
            "        }",
            "        return bundle",
            "    }()",
            "}",
            "",
        ]
    )


def embedded_variable(path: Path) -> str:
    mangled = re.sub(r"[^A-Za-z0-9_]", "_", path.name)
    return f"_{mangled}" if mangled[:1].isdigit() else mangled


def embedded_source(resources: Sequence[PackageResource]) -> str:
    lines = ["struct PackageResources {"]
    for resource in resources:
        data = ",".join(str(b) for b in resource.path.read_bytes())
        lines.append(f"static let {embedded_variable(resource.path)}: [UInt8] = [{data}]")
    lines.extend(["}", ""])
    return "\n".join(lines)


def bundle_info(name: str, region: str | None, deployment_target: str) -> dict[str, Any]:
    stem = name[: -len(BUNDLE_SUFFIX)]
    identifier = RE_BUNDLE_ID_UNSAFE.sub("-", stem.replace("_", ".")) + ".resources"
    return {
        "CFBundleDevelopmentRegion": region or "en",
        "CFBundleIdentifier": identifier,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": stem,
        "CFBundlePackageType": "BNDL",
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "MinimumOSVersion": deployment_target,
    }


def resource_files(resources: Sequence[PackageResource]) -> list[Path]:
    files: list[Path] = []
    for resource in resources:
        if resource.path.is_dir():
            files.extend(p for p in sorted(resource.path.rglob("*")) if p.is_file())
        else:
            files.append(resource.path)
    return files


def _copy_tree(source: Path, target: Path) -> None:
    for item in sorted(source.rglob("*")):
        if item.is_file():
            landing = target / item.relative_to(source)
            ensure_dir(landing.parent)
            shutil.copyfile(item, landing)


def _place(resource: PackageResource, bundle: Path) -> None:
    landing = destination(resource)
    if landing is None:
        return
    target = bundle / landing
    ensure_dir(target.parent)
    suffix = resource.path.suffix.lower()
    if resource.rule == COPY and resource.path.is_dir():
        _copy_tree(resource.path, target)
    elif resource.rule == COPY:
        shutil.copyfile(resource.path, target)
    elif suffix == ".strings":
        strings.compile_strings_file(str(resource.path), str(target))
    elif suffix == ".stringsdict":
        strings.compile_plist_source(str(resource.path), str(target))
    elif suffix == ".xib":
        target.write_bytes(xib.compile_xib(resource.path.read_text(encoding="utf-8")))
    elif suffix == ".storyboard":
        storyboard.write_storyboardc(resource.path.read_text(encoding="utf-8"), str(target))
    elif resource.path.is_dir():
        _copy_tree(resource.path, target)
    else:
        shutil.copyfile(resource.path, target)


def assemble_bundle(
    resources: Sequence[PackageResource],
    bundle: Path,
    region: str | None,
    deployment_target: str,
) -> list[Path]:
    staging = bundle.with_name(bundle.name + ".partial")
    rmtree_force(staging)
    ensure_dir(staging)
    (staging / "Info.plist").write_bytes(
        write_plist(bundle_info(bundle.name, region, deployment_target), binary=True)
    )

    catalogs = [r.path for r in resources if r.path.suffix.lower() == assets.CATALOG_SUFFIX]
    reporter = get_reporter()
    if catalogs:
        result = assets.compile_catalogs(catalogs, staging, platform_version=deployment_target)
        for warning in result.warnings:
            reporter.warn(f"{bundle.name} {warning}")
        for item in result.lost:
            reporter.warn(f"{bundle.name} dropped {item.get('asset', item.get('path'))}")

    for resource in resources:
        if resource.path.suffix.lower() == ".xcstrings":
            strings.emit_lproj_from_xcstrings(
                str(resource.path), str(staging), table_name=resource.path.stem
            )
        else:
            _place(resource, staging)

    rmtree_force(bundle)
    os.replace(staging, bundle)
    return sorted(p for p in bundle.rglob("*") if p.is_file())
