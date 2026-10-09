from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib
from typing import Any

from iosc.core import ManifestError

MANIFEST_NAME = "iosc.toml"

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
    "dependencies",
}

SEMVER = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
RE_SEMVER = re.compile(rf"^{SEMVER}$")
RE_RANGE = re.compile(rf"^({SEMVER})(\.\.<|\.\.\.)({SEMVER})$")
RE_REVISION = re.compile(r"^[0-9a-fA-F]{7,40}$")

REQUIREMENT_KINDS = ("from", "exact", "range", "branch", "revision")
DEPENDENCY_KEYS = {"path", "url", "products", *REQUIREMENT_KINDS}


@dataclass(frozen=True)
class Requirement:
    kind: str
    value: str


@dataclass(frozen=True)
class Dependency:
    name: str
    path: str | None = None
    url: str | None = None
    requirement: Requirement | None = None
    # empty selects the product named after the package
    products: tuple[str, ...] = ()


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
    dependencies: tuple[Dependency, ...] = ()


def _semver_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[-+]", text)[0].split("."))


def _check_range(name: str, value: str) -> None:
    match = RE_RANGE.match(value)
    if not match:
        raise ManifestError(
            f"dependency '{name}' range '{value}' must look like \"1.2.0..<2.0.0\" or \"1.2.0...1.4.0\""
        )
    lower, operator, upper = (_semver_key(match[1]), match[2], _semver_key(match[3]))
    if lower > upper or (operator == "..<" and lower == upper):
        raise ManifestError(f"dependency '{name}' range '{value}' contains no versions")


def parse_requirement(name: str, spec: dict[str, Any]) -> Requirement:
    given = [kind for kind in REQUIREMENT_KINDS if kind in spec]
    if len(given) != 1:
        raise ManifestError(
            f"dependency '{name}' needs exactly one of {', '.join(REQUIREMENT_KINDS)}"
        )
    kind = given[0]
    value = spec[kind]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"dependency '{name}' '{kind}' must be a string")
    if kind in ("from", "exact") and not RE_SEMVER.match(value):
        raise ManifestError(f"dependency '{name}' {kind} '{value}' is not a version like 1.2.3")
    if kind == "range":
        _check_range(name, value)
    if kind == "revision" and not RE_REVISION.match(value):
        raise ManifestError(f"dependency '{name}' revision '{value}' is not a commit hash")
    return Requirement(kind=kind, value=value)


def parse_dependencies(raw: Any) -> tuple[Dependency, ...]:
    if not isinstance(raw, dict):
        raise ManifestError("'dependencies' must be a table of name = { url = \"...\", from = \"...\" }")

    dependencies = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ManifestError(
                f"dependency '{name}' must be a table like {{ path = \"../{name}\" }}"
            )
        if "id" in spec:
            raise ManifestError(f"dependency '{name}' uses a registry id, which iosc does not support yet")
        unknown = sorted(spec.keys() - DEPENDENCY_KEYS)
        if unknown:
            raise ManifestError(f"dependency '{name}' has unknown key '{unknown[0]}'")

        products = spec.get("products", [])
        if not isinstance(products, list) or not all(
            isinstance(p, str) and p.strip() for p in products
        ):
            raise ManifestError(f"dependency '{name}' 'products' must be a list of product names")

        path, url = spec.get("path"), spec.get("url")
        if (path is None) == (url is None):
            raise ManifestError(f"dependency '{name}' needs either a 'path' or a 'url'")

        if path is not None:
            if not isinstance(path, str) or not path.strip():
                raise ManifestError(f"dependency '{name}' 'path' must be a string")
            versioned = [kind for kind in REQUIREMENT_KINDS if kind in spec]
            if versioned:
                raise ManifestError(
                    f"dependency '{name}' is a path dependency and takes no '{versioned[0]}'"
                )
            dependencies.append(Dependency(name=name, path=path, products=tuple(products)))
            continue

        if not isinstance(url, str) or not url.strip():
            raise ManifestError(f"dependency '{name}' 'url' must be a string")
        dependencies.append(
            Dependency(
                name=name,
                url=url,
                requirement=parse_requirement(name, spec),
                products=tuple(products),
            )
        )
    return tuple(dependencies)


def path_for(project_dir: Path | str) -> Path:
    return Path(project_dir) / MANIFEST_NAME


def load(project_dir: Path) -> Manifest:
    manifest_path = path_for(project_dir)
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

    dependencies = parse_dependencies(data.get("dependencies", {}))

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
        dependencies=dependencies,
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
        f"[info_plist]\n\n"
        f"# Swift packages, e.g. Collections = {{ url = \"https://github.com/apple/swift-collections\", from = \"1.1.0\" }}\n"
        f"[dependencies]\n"
    )


RE_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
RE_DEPENDENCIES_HEADER = re.compile(r"^\s*\[\s*dependencies\s*\]\s*(#.*)?$")
RE_TABLE_HEADER = re.compile(r"^\s*\[")


def toml_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def toml_key(name: str) -> str:
    return name if RE_BARE_KEY.match(name) else toml_string(name)


def dependency_line(dependency: Dependency) -> str:
    fields: list[str] = []
    if dependency.path is not None:
        fields.append(f"path = {toml_string(dependency.path)}")
    if dependency.url is not None:
        fields.append(f"url = {toml_string(dependency.url)}")
    if dependency.requirement is not None:
        fields.append(f"{dependency.requirement.kind} = {toml_string(dependency.requirement.value)}")
    if dependency.products:
        listed = ", ".join(toml_string(p) for p in dependency.products)
        fields.append(f"products = [{listed}]")
    return f"{toml_key(dependency.name)} = {{ {', '.join(fields)} }}"


# line edit keeps user comments and layout
def add_dependency(text: str, dependency: Dependency) -> str:
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ManifestError(f"iosc.toml has a syntax error, fix it before adding a package ({err})")
    existing = before.get("dependencies", {})
    if isinstance(existing, dict) and dependency.name in existing:
        raise ManifestError(f"dependency '{dependency.name}' is already in iosc.toml")

    line = dependency_line(dependency)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    header = next((i for i, l in enumerate(lines) if RE_DEPENDENCIES_HEADER.match(l)), None)
    if header is None:
        if "dependencies" in before:
            raise ManifestError(
                "iosc.toml declares dependencies without a [dependencies] table, add the package by hand"
            )
        body = lines + ([""] if lines and lines[-1].strip() else []) + ["[dependencies]", line]
    else:
        end = next(
            (i for i in range(header + 1, len(lines)) if RE_TABLE_HEADER.match(lines[i])),
            len(lines),
        )
        last_entry = header
        for i in range(header + 1, end):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#"):
                last_entry = i
        body = lines[: last_entry + 1] + [line] + lines[last_entry + 1 :]
    edited = newline.join(body) + newline

    try:
        after = tomllib.loads(edited)
    except tomllib.TOMLDecodeError as err:
        raise ManifestError(f"adding '{dependency.name}' would break iosc.toml ({err}), add it by hand")
    parse_dependencies(after.get("dependencies", {}))
    if dependency.name not in after.get("dependencies", {}):
        raise ManifestError(f"could not place '{dependency.name}' in iosc.toml, add it by hand")
    return edited


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
