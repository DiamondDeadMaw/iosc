from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib
from typing import Any

from iosc.core import ManifestError

RE_BUNDLE_ID = re.compile(r"^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$")
RE_DEPLOYMENT_TARGET = re.compile(r"^\d+(\.\d+)*$")
RE_VERSION = re.compile(r"^\d+(\.\d+)*$")

KNOWN_MANIFEST_KEYS = {
    "name",
    "bundle_id",
    "version",
    "build",
    "deployment_target",
    "sources",
    "resources",
    "frameworks",
    "entitlements",
    "info_plist",
    "swift_flags",
    "linker_flags",
}


@dataclass(frozen=True)
class Manifest:
    name: str
    bundle_id: str
    version: str = "1.0.0"
    build: str = "1"
    deployment_target: str = "17.0"
    sources: list[str] = field(default_factory=lambda: ["Sources/**/*.swift"])
    resources: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=lambda: ["Foundation", "UIKit"])
    entitlements: dict[str, Any] = field(default_factory=dict)
    info_plist: dict[str, Any] = field(default_factory=dict)
    swift_flags: list[str] = field(default_factory=list)
    linker_flags: list[str] = field(default_factory=list)


def load(project_dir: Path) -> Manifest:
    manifest_path = Path(project_dir) / "iosc.toml"
    if not manifest_path.exists():
        raise ManifestError(f"manifest not found at {manifest_path}")

    try:
        content = manifest_path.read_bytes()
        data = tomllib.loads(content.decode("utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ManifestError(f"syntax error reading {manifest_path} with {err}")
    except UnicodeDecodeError as err:
        raise ManifestError(f"manifest at {manifest_path} is not valid UTF-8 with {err}")

    for key in data:
        if key not in KNOWN_MANIFEST_KEYS:
            raise ManifestError(f"unknown manifest key '{key}'")

    if "name" not in data:
        raise ManifestError("manifest missing required key 'name'")
    name = data["name"]
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("manifest key 'name' must be a non-empty string")

    if "bundle_id" not in data:
        raise ManifestError("manifest missing required key 'bundle_id'")
    bundle_id = data["bundle_id"]
    if not isinstance(bundle_id, str) or not RE_BUNDLE_ID.match(bundle_id):
        raise ManifestError(
            f"invalid bundle_id '{bundle_id}', must match reverse DNS pattern of dot separated segments of letters digits and hyphens"
        )

    if "version" in data:
        version = str(data["version"])
        if not RE_VERSION.match(version):
            raise ManifestError(
                f"invalid version '{version}', must be dot separated numbers"
            )
    else:
        version = "1.0.0"

    if "build" in data:
        build = str(data["build"])
    else:
        build = "1"

    if "deployment_target" in data:
        deployment_target = str(data["deployment_target"])
        if not RE_DEPLOYMENT_TARGET.match(deployment_target):
            raise ManifestError(
                f"invalid deployment_target '{deployment_target}', must look like a version number"
            )
    else:
        deployment_target = "17.0"

    sources = data.get("sources", ["Sources/**/*.swift"])
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise ManifestError("'sources' must be a list of path glob strings")

    resources = data.get("resources", [])
    if not isinstance(resources, list) or not all(isinstance(r, str) for r in resources):
        raise ManifestError("'resources' must be a list of path glob strings")

    frameworks = data.get("frameworks", ["Foundation", "UIKit"])
    if not isinstance(frameworks, list) or not all(isinstance(f, str) for f in frameworks):
        raise ManifestError("'frameworks' must be a list of framework name strings")

    entitlements = data.get("entitlements", {})
    if not isinstance(entitlements, dict):
        raise ManifestError("'entitlements' must be a table")

    info_plist = data.get("info_plist", {})
    if not isinstance(info_plist, dict):
        raise ManifestError("'info_plist' must be a table")

    swift_flags = data.get("swift_flags", [])
    if not isinstance(swift_flags, list) or not all(isinstance(f, str) for f in swift_flags):
        raise ManifestError("'swift_flags' must be a list of flag strings")

    linker_flags = data.get("linker_flags", [])
    if not isinstance(linker_flags, list) or not all(isinstance(f, str) for f in linker_flags):
        raise ManifestError("'linker_flags' must be a list of flag strings")

    return Manifest(
        name=name,
        bundle_id=bundle_id,
        version=version,
        build=build,
        deployment_target=deployment_target,
        sources=sources,
        resources=resources,
        frameworks=frameworks,
        entitlements=entitlements,
        info_plist=info_plist,
        swift_flags=swift_flags,
        linker_flags=linker_flags,
    )


def default_manifest(name: str, bundle_id: str) -> str:
    return (
        f"# Project configuration\n"
        f'name = "{name}"\n'
        f'bundle_id = "{bundle_id}"\n'
        f'version = "1.0.0"\n'
        f'build = "1"\n'
        f'deployment_target = "17.0"\n\n'
        f"# Source files\n"
        f'sources = ["Sources/**/*.swift"]\n\n'
        f"# Resource files\n"
        f"resources = []\n\n"
        f"# Linked frameworks\n"
        f'frameworks = ["Foundation", "UIKit"]\n\n'
        f"# Extra compiler flags\n"
        f"swift_flags = []\n\n"
        f"# Extra linker flags, e.g. [\"-lsqlite3\"]\n"
        f"linker_flags = []\n\n"
        f"# Tables last, every key below one belongs to it\n"
        f"# Signing entitlements\n"
        f"[entitlements]\n\n"
        f"# Extra property list entries\n"
        f"[info_plist]\n"
    )


def resolve_sources(manifest: Manifest, project_dir: Path) -> list[Path]:
    resolved_dir = Path(project_dir)
    collected: set[Path] = set()

    for pattern in manifest.sources:
        matched = [p for p in resolved_dir.glob(pattern) if p.is_file()]
        if not matched:
            raise ManifestError(
                f"source glob '{pattern}' matched no files in {resolved_dir}"
            )
        for p in matched:
            collected.add(p)

    return sorted(collected)
