from collections.abc import Sequence
import os
from pathlib import Path
import re
import shutil
from typing import Any

from iosc.build.graph import BuildLayout, Stage, fingerprint, relative_key
from iosc.config.manifest import Manifest
from iosc.core import BundleError, rmtree_force
from iosc.core.errors import MachOError
from iosc.formats import macho
from iosc.formats.plist import read_plist, write_plist

STAGE_NAME = "bundle"
PKG_INFO = b"APPL????"
PARTIAL_PLIST_NAME = "assets-partial.plist"

REQUIRED_KEYS = (
    "CFBundleName",
    "CFBundleIdentifier",
    "CFBundleVersion",
    "CFBundleShortVersionString",
    "CFBundleExecutable",
    "MinimumOSVersion",
)

# re-bound from iosc.formats.macho
MH_MAGIC_64 = macho.MH_MAGIC_64_BYTES
CPU_TYPE_ARM64 = macho.CPU_TYPE_ARM64
MH_EXECUTE = macho.MH_EXECUTE

RE_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def product_name(manifest: Manifest) -> str:
    name = RE_UNSAFE.sub("", manifest.name.strip())
    if not name:
        raise BundleError(
            f"manifest name '{manifest.name}' has no characters usable in a bundle name"
        )
    return name


def partial_plist_path(layout: BuildLayout) -> Path:
    return layout.root / PARTIAL_PLIST_NAME


def default_info_plist(manifest: Manifest, executable: str) -> dict[str, Any]:
    return {
        "CFBundleName": manifest.name,
        "CFBundleDisplayName": manifest.name,
        "CFBundleIdentifier": manifest.bundle_id,
        "CFBundleVersion": manifest.build,
        "CFBundleShortVersionString": manifest.version,
        "CFBundleExecutable": executable,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "MinimumOSVersion": manifest.deployment_target,
        "DTPlatformName": "iphoneos",
        "LSRequiresIPhoneOS": True,
        "UIRequiredDeviceCapabilities": ["arm64"],
        "UILaunchScreen": {},
    }


# merge nested dicts one level deep. a partial CFBundlePrimaryIcon keeps its siblings
def merge_partial(info: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    merged = dict(info)
    for key, value in partial.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            combined = dict(existing)
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value
    return merged


def build_info_plist(
    manifest: Manifest,
    executable: str,
    asset_partial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    info = default_info_plist(manifest, executable)
    if asset_partial:
        info = merge_partial(info, asset_partial)
    # apply manifest table last to win ties
    if manifest.info_plist:
        info = merge_partial(info, manifest.info_plist)
    validate_info_plist(info)
    return info


def validate_info_plist(info: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in info]
    if missing:
        raise BundleError(f"Info.plist missing required keys {missing}")
    executable = info["CFBundleExecutable"]
    if executable != executable.strip():
        raise BundleError("CFBundleExecutable has leading or trailing whitespace")


def read_macho_arch(executable_path: Path | str) -> tuple[str, str]:
    with open(executable_path, "rb") as f:
        head = f.read(macho.HEADER_SIZE)
    try:
        macho.require_arm64_executable(head)
    except MachOError as err:
        raise BundleError(f"{executable_path} {err}") from err
    return "arm64", "executable"


def read_asset_partial(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    parsed = read_plist(path.read_bytes())
    if not isinstance(parsed, dict):
        raise BundleError(f"asset partial at {path} is not a dictionary")
    return parsed


COMPILED_SUFFIXES = (".xcassets", ".xcstrings", ".strings", ".stringsdict",
                     ".xib", ".storyboard")


# skip files a compile stage produces
def collect_resources(manifest: Manifest, project: Path) -> list[Path]:
    collected: set[Path] = set()
    for pattern in manifest.resources:
        for match in project.glob(pattern):
            if not match.is_file():
                continue
            if match.suffix.lower() in COMPILED_SUFFIXES:
                continue
            if any(p.lower().endswith(".xcassets") for p in match.parts):
                continue
            collected.add(match)
    return sorted(collected)


def _copy_tree_into(source: Path, dest: Path) -> None:
    if not source.is_dir():
        return
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        target = dest / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)


def assemble(
    manifest: Manifest,
    layout: BuildLayout,
    executable: Path,
    product: str,
) -> Path:
    read_macho_arch(executable)
    info = build_info_plist(
        manifest, product, read_asset_partial(partial_plist_path(layout))
    )

    staging_app = layout.staging / f"{product}.app"
    rmtree_force(staging_app)
    staging_app.mkdir(parents=True)

    destination = staging_app / product
    shutil.copyfile(executable, destination)
    os.chmod(destination, 0o755)

    (staging_app / "Info.plist").write_bytes(write_plist(info, binary=True))
    (staging_app / "PkgInfo").write_bytes(PKG_INFO)

    _copy_tree_into(layout.resources, staging_app)
    for resource in collect_resources(manifest, layout.project):
        target = staging_app / resource.relative_to(layout.project)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resource, target)

    # swap in only after the whole tree is assembled
    rmtree_force(layout.app)
    layout.app.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_app, layout.app)
    return layout.app


def verify(app_dir: Path | str) -> list[str]:
    app = Path(app_dir)
    problems: list[str] = []

    info_path = app / "Info.plist"
    if not info_path.is_file():
        return ["missing Info.plist"]

    info = read_plist(info_path.read_bytes())
    try:
        validate_info_plist(info)
    except BundleError as err:
        problems.append(str(err))

    executable = info.get("CFBundleExecutable")
    if executable:
        exe_path = app / executable
        if not exe_path.is_file():
            problems.append(f"missing executable {executable}")
        else:
            try:
                read_macho_arch(exe_path)
            except BundleError as err:
                problems.append(str(err))

    pkg_info = app / "PkgInfo"
    if not pkg_info.is_file():
        problems.append("missing PkgInfo")
    elif pkg_info.read_bytes() != PKG_INFO:
        problems.append(
            f"PkgInfo content is {pkg_info.read_bytes()!r}, expected {PKG_INFO!r}"
        )

    return problems


def bundle_stage(
    manifest: Manifest,
    layout: BuildLayout,
    executable: Path,
    product: str,
    depends_on: Sequence[str] = (),
) -> Stage:
    resources = collect_resources(manifest, layout.project)

    # layout.resources files arrive via a dependency, not as declared inputs
    # they may not exist yet when this stage is planned
    print_ = fingerprint(
        {
            "info_plist": build_info_plist(manifest, product),
            "resources": [relative_key(p, layout.project) for p in resources],
        }
    )

    def run() -> None:
        assemble(manifest, layout, executable, product)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        depends_on=tuple(depends_on),
        inputs=(executable, *resources),
        # executable is deliberately not an output. sign rewrites it in place
        outputs=(layout.app / "Info.plist", layout.app / "PkgInfo"),
    )
