from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from iosc.build import package_resources
from iosc.build.graph import (
    BuildLayout,
    Stage,
    fingerprint,
    read_output_manifest,
    relative_key,
    write_output_manifest,
)
from iosc.build.package_resources import PackageResource
from iosc.config.manifest import Dependency, Manifest
from iosc.core import PackageError, get_reporter, sha256_file, write_atomic
from iosc.core.progress import Heartbeat
from iosc.toolchain import msvc, swiftpm
from iosc.toolchain.discovery import Toolchain, find_git
from iosc.toolchain.swift import CompileSpec, compile_objects

GRAPH_VERSION = 3
ROOT_PACKAGE = "iosc-dependencies"
LOCK_FILE = "Package.resolved"
STAGE_PREFIX = "package "
RESOURCES_SUFFIX = " resources"
PLATFORM = "ios"
CONFIGURATION = "debug"
PACKAGE_DEFINES = ("SWIFT_PACKAGE", "DEBUG")
SUPPORTED_LANGUAGE_MODES = ("4", "4.2", "5", "6")
# warning flags only change diagnostics, swiftpm drops them for deps
IGNORED_SWIFT_SETTINGS = {
    "treatAllWarnings",
    "treatWarning",
    "enableWarning",
    "disableWarning",
    "strictMemorySafety",
}


@dataclass(frozen=True)
class PackageTarget:
    package: str
    name: str
    module: str
    directory: Path
    sources: tuple[Path, ...]
    dependencies: tuple[str, ...]
    swift_version: str
    package_name: str | None = None
    defines: tuple[str, ...] = ()
    upcoming_features: tuple[str, ...] = ()
    experimental_features: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    libraries: tuple[str, ...] = ()
    resources: tuple[PackageResource, ...] = ()
    # bundle name, None when only embedded resources
    bundle: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class PackageGraph:
    # dependency order, imports first
    targets: tuple[PackageTarget, ...] = ()
    package_roots: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def frameworks(self) -> list[str]:
        return _ordered_union(t.frameworks for t in self.targets)

    @property
    def libraries(self) -> list[str]:
        return _ordered_union(t.libraries for t in self.targets)


def _ordered_union(groups: Iterable[Sequence[str]]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for item in group:
            if item not in seen:
                seen.append(item)
    return seen


def packages_dir(layout: BuildLayout) -> Path:
    return layout.root / "packages"


def module_dir(layout: BuildLayout) -> Path:
    return packages_dir(layout) / "modules"


def object_path(layout: BuildLayout, module: str) -> Path:
    return packages_dir(layout) / "obj" / f"{module}.o"


def derived_dir(layout: BuildLayout, module: str) -> Path:
    return packages_dir(layout) / "derived" / module


def bundle_path(layout: BuildLayout, name: str) -> Path:
    return packages_dir(layout) / "bundles" / name


def stage_name(module: str) -> str:
    return f"{STAGE_PREFIX}{module}"


def resources_stage_name(module: str) -> str:
    return f"{STAGE_PREFIX}{module}{RESOURCES_SUFFIX}"


def c99_name(text: str) -> str:
    mangled = re.sub(r"[^A-Za-z0-9_]", "_", text)
    return f"_{mangled}" if mangled[:1].isdigit() else mangled


def swift_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def package_declaration(dependency: Dependency, path: Path | None) -> str:
    if path is not None:
        return f".package(path: {swift_string(path.as_posix())})"
    requirement = dependency.requirement
    if dependency.url is None or requirement is None:
        raise PackageError(f"dependency '{dependency.name}' has neither a path nor a url")
    url = swift_string(dependency.url)
    if requirement.kind == "range":
        match = re.match(r"^(.+?)(\.\.<|\.\.\.)(.+)$", requirement.value)
        if match is None:
            raise PackageError(f"dependency '{dependency.name}' has a malformed range")
        lower, operator, upper = match.groups()
        return f".package(url: {url}, {swift_string(lower)}{operator}{swift_string(upper)})"
    return f".package(url: {url}, {requirement.kind}: {swift_string(requirement.value)})"


# targetless root for swiftpm
def render_root_manifest(declarations: Sequence[str]) -> str:
    lines = [
        "// swift-tools-version:5.9",
        "import PackageDescription",
        "",
        "let package = Package(",
        f"    name: {swift_string(ROOT_PACKAGE)},",
        "    dependencies: [",
    ]
    lines.extend(f"        {declaration}," for declaration in declarations)
    lines.extend(["    ]", ")", ""])
    return "\n".join(lines)


def _same_path(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def _url_key(url: str) -> str:
    key = url.strip().rstrip("/").lower()
    return key[: -len(".git")] if key.endswith(".git") else key


def dependency_path(dependency: Dependency, project: Path) -> Path | None:
    if dependency.path is None:
        return None
    path = (Path(project) / dependency.path).resolve()
    if not (path / "Package.swift").is_file():
        raise PackageError(
            f"dependency '{dependency.name}' points at {path}, which has no Package.swift"
        )
    return path


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _condition_applies(condition: Any) -> bool:
    if not isinstance(condition, dict):
        return True
    platforms = condition.get("platformNames") or []
    if platforms and PLATFORM not in platforms:
        return False
    config = condition.get("config")
    return config is None or config == CONFIGURATION


@dataclass
class _Package:
    identity: str
    path: Path
    url: str
    children: list[str]
    description: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.description.get("name", self.identity))

    @property
    def display_name(self) -> str:
        return str(self.description.get("manifest_display_name", self.name))

    def described_target(self, name: str) -> dict[str, Any] | None:
        for target in self.description.get("targets", []):
            if target.get("name") == name:
                return target
        return None

    def declared_target(self, name: str) -> dict[str, Any] | None:
        for target in self.manifest.get("targets", []):
            if target.get("name") == name:
                return target
        return None

    def product(self, name: str) -> dict[str, Any] | None:
        for product in self.description.get("products", []):
            if product.get("name") == name:
                return product
        return None

    def library_products(self) -> list[str]:
        return [
            p["name"]
            for p in self.description.get("products", [])
            if isinstance(p.get("type"), dict) and "library" in p["type"]
        ]


@dataclass(frozen=True)
class _Node:
    path: Path
    url: str
    children: list[str]


def _flatten_tree(node: dict[str, Any], into: dict[str, _Node]) -> None:
    for child in node.get("dependencies", []):
        identity = child["identity"]
        if identity not in into:
            into[identity] = _Node(
                path=Path(child["path"]),
                url=str(child.get("url", "")),
                children=[grandchild["identity"] for grandchild in child.get("dependencies", [])],
            )
            _flatten_tree(child, into)


class _GraphBuilder:
    def __init__(self, packages: dict[str, _Package], deployment_target: str) -> None:
        self.packages = packages
        self.deployment_target = deployment_target
        self.ordered: list[PackageTarget] = []
        self.visiting: set[tuple[str, str]] = set()
        self.done: dict[tuple[str, str], str] = {}
        self.warned: set[tuple[str, str]] = set()

    def fail(self, package: _Package, target: str, reason: str) -> PackageError:
        return PackageError(f"package {package.identity} target {target} {reason}")

    def add_product(self, package: _Package, product_name: str) -> list[str]:
        product = package.product(product_name)
        if product is None:
            available = ", ".join(package.library_products()) or "none"
            raise PackageError(
                f"package {package.identity} has no product {product_name}, "
                f"its library products are {available}"
            )
        kind = product.get("type")
        if not isinstance(kind, dict) or "library" not in kind:
            raise PackageError(
                f"package {package.identity} product {product_name} is not a library"
            )
        key = (package.identity, product_name)
        if kind["library"] == ["dynamic"] and key not in self.warned:
            self.warned.add(key)
            get_reporter().warn(
                f"package {package.identity} product {product_name} asks for a dynamic "
                f"library, iosc links it statically"
            )
        return [self.add_target(package, target) for target in product.get("targets", [])]

    def _package_for_product(self, package: _Package, reference: str) -> _Package:
        wanted = reference.lower()
        for identity in package.children:
            child = self.packages[identity]
            if child.identity.lower() == wanted or child.name.lower() == wanted:
                return child
        raise PackageError(
            f"package {package.identity} names package {reference}, which it does not depend on"
        )

    def _package_with_product(self, package: _Package, target: str, name: str) -> _Package:
        for identity in package.children:
            child = self.packages[identity]
            if child.product(name) is not None:
                return child
        raise self.fail(package, target, f"depends on {name}, which is neither a target nor a product")

    def _dependencies(self, package: _Package, target: str, declared: dict[str, Any]) -> list[str]:
        modules: list[str] = []
        for entry in declared.get("dependencies", []):
            if not isinstance(entry, dict) or len(entry) != 1:
                raise self.fail(package, target, f"has a dependency iosc cannot read, {entry}")
            kind, payload = next(iter(entry.items()))
            if not isinstance(payload, list) or not payload:
                raise self.fail(package, target, f"has a dependency iosc cannot read, {entry}")
            name = payload[0]
            if kind in ("byName", "target"):
                if not _condition_applies(payload[1] if len(payload) > 1 else None):
                    continue
                if package.described_target(name) is not None:
                    modules.append(self.add_target(package, name))
                elif kind == "target":
                    raise self.fail(package, target, f"depends on missing target {name}")
                else:
                    child = self._package_with_product(package, target, name)
                    modules.extend(self.add_product(child, name))
            elif kind == "product":
                reference = payload[1] if len(payload) > 1 else None
                aliases = payload[2] if len(payload) > 2 else None
                if not _condition_applies(payload[3] if len(payload) > 3 else None):
                    continue
                if aliases:
                    raise self.fail(package, target, "uses module aliases, which iosc does not support")
                child = (
                    self._package_for_product(package, reference)
                    if reference
                    else self._package_with_product(package, target, name)
                )
                modules.extend(self.add_product(child, name))
            else:
                raise self.fail(package, target, f"has an unknown dependency kind {kind}")
        return modules

    def _check_kind(self, package: _Package, name: str, described: dict[str, Any]) -> None:
        module_type = described.get("module_type")
        kind = described.get("type")
        if module_type == "ClangTarget":
            raise self.fail(package, name, "is C or Objective-C, which iosc does not build yet")
        if module_type == "BinaryTarget" or kind == "binary":
            raise self.fail(package, name, "is a prebuilt binary, which iosc does not link yet")
        if kind == "macro":
            raise self.fail(package, name, "is a Swift macro, which iosc does not build yet")
        if module_type != "SwiftTarget" or kind != "library":
            raise self.fail(package, name, f"is a {kind} target, only Swift libraries can be linked")

    def _resources(self, package: _Package, name: str, described: dict[str, Any]) -> list[PackageResource]:
        entries = described.get("resources") or []
        if not isinstance(entries, list):
            raise self.fail(package, name, f"has resources iosc cannot read, {entries}")
        resources = [package_resources.parse_rule(entry) for entry in entries]
        for resource in resources:
            reason = package_resources.unsupported_reason(resource)
            if reason is not None:
                raise self.fail(package, name, reason)
        clash = package_resources.duplicate_destination(resources)
        if clash is not None:
            raise self.fail(package, name, f"has more than one resource landing at {clash}")
        return resources

    def _language_mode(self, package: _Package, override: str | None) -> str:
        if override is not None:
            if override not in SUPPORTED_LANGUAGE_MODES:
                raise PackageError(f"package {package.identity} asks for Swift language mode {override}")
            return override
        declared = package.manifest.get("swiftLanguageVersions") or []
        supported = [m for m in declared if m in SUPPORTED_LANGUAGE_MODES]
        if supported:
            return max(supported, key=_version_tuple)
        if declared:
            raise PackageError(
                f"package {package.identity} supports Swift language modes {', '.join(declared)}, "
                f"none of which this compiler knows"
            )
        tools = package.manifest.get("toolsVersion", {}).get("_version", "5.0")
        return "6" if _version_tuple(tools) >= (6,) else "5"

    def _platform_check(self, package: _Package) -> None:
        for platform in package.description.get("platforms", []):
            if platform.get("name") != PLATFORM:
                continue
            needed = str(platform.get("version", "0"))
            if _version_tuple(needed) > _version_tuple(self.deployment_target):
                raise PackageError(
                    f"package {package.identity} needs iOS {needed}, "
                    f"the project deploys to {self.deployment_target}"
                )

    def add_target(self, package: _Package, name: str) -> str:
        key = (package.identity, name)
        if key in self.done:
            return self.done[key]
        if key in self.visiting:
            raise self.fail(package, name, "is part of a dependency cycle")
        self.visiting.add(key)

        described = package.described_target(name)
        declared = package.declared_target(name)
        if described is None or declared is None:
            raise self.fail(package, name, "is missing from the package description")
        self._check_kind(package, name, described)
        if declared.get("pluginUsages"):
            raise self.fail(package, name, "uses build tool plugins, which iosc does not run")
        self._platform_check(package)

        dependencies = self._dependencies(package, name, declared)

        defines: list[str] = []
        upcoming: list[str] = []
        experimental: list[str] = []
        frameworks: list[str] = []
        libraries: list[str] = []
        language_override: str | None = None
        for setting in declared.get("settings", []):
            tool = setting.get("tool")
            if tool not in ("swift", "linker"):
                continue
            if not _condition_applies(setting.get("condition")):
                continue
            raw_kind = setting.get("kind")
            if not isinstance(raw_kind, dict) or len(raw_kind) != 1:
                raise self.fail(package, name, f"has a setting iosc cannot read, {setting}")
            kind, payload = next(iter(raw_kind.items()))
            value = payload.get("_0") if isinstance(payload, dict) else None
            if kind == "define":
                defines.append(value)
            elif kind == "enableUpcomingFeature":
                upcoming.append(value)
            elif kind == "enableExperimentalFeature":
                experimental.append(value)
            elif kind == "swiftLanguageMode":
                language_override = str(value)
            elif kind == "linkedFramework":
                frameworks.append(value)
            elif kind == "linkedLibrary":
                libraries.append(value)
            elif kind in IGNORED_SWIFT_SETTINGS:
                continue
            elif kind == "unsafeFlags":
                raise self.fail(package, name, "uses unsafeFlags, which iosc does not pass through")
            else:
                raise self.fail(package, name, f"uses the {kind} setting, which iosc does not support yet")

        target_dir = package.path / described["path"]
        sources = tuple(target_dir / source for source in described.get("sources", []))
        if not sources:
            raise self.fail(package, name, "has no sources")

        resources = self._resources(package, name, described)
        bundle = (
            package_resources.bundle_name(package.display_name, name)
            if package_resources.bundled(resources)
            else None
        )

        module = described.get("c99name", c99_name(name))
        for existing in self.ordered:
            if existing.module == module:
                raise PackageError(
                    f"module {module} comes from both package {existing.package} "
                    f"and package {package.identity}"
                )

        self.ordered.append(
            PackageTarget(
                package=package.identity,
                name=name,
                module=module,
                directory=target_dir,
                sources=sources,
                dependencies=tuple(dict.fromkeys(dependencies)),
                swift_version=self._language_mode(package, language_override),
                package_name=c99_name(package.identity) if declared.get("packageAccess") else None,
                defines=tuple(defines),
                upcoming_features=tuple(upcoming),
                experimental_features=tuple(experimental),
                frameworks=tuple(frameworks),
                libraries=tuple(libraries),
                resources=tuple(resources),
                bundle=bundle,
                region=package.description.get("default_localization"),
            )
        )
        self.visiting.discard(key)
        self.done[key] = module
        return module


def default_products(dependency: Dependency, package: _Package) -> list[str]:
    libraries = package.library_products()
    for preferred in (dependency.name, package.name):
        if preferred in libraries:
            return [preferred]
    if not libraries:
        raise PackageError(f"package {package.identity} has no library products")
    return libraries


def _top_level_package(
    dependency: Dependency, path: Path | None, packages: dict[str, _Package]
) -> _Package:
    for package in packages.values():
        if path is not None and _same_path(package.path, path):
            return package
        if dependency.url is not None and _url_key(package.url) == _url_key(dependency.url):
            return package
    where = path if path is not None else dependency.url
    raise PackageError(f"swift package did not resolve dependency '{dependency.name}' at {where}")


def build_graph(
    dependencies: Sequence[tuple[Dependency, Path | None]],
    tree: dict[str, Any],
    descriptions: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    deployment_target: str,
) -> PackageGraph:
    flat: dict[str, _Node] = {}
    _flatten_tree(tree, flat)
    packages = {
        identity: _Package(
            identity, node.path, node.url, node.children, descriptions[identity], manifests[identity]
        )
        for identity, node in flat.items()
    }

    builder = _GraphBuilder(packages, deployment_target)
    for dependency, path in dependencies:
        package = _top_level_package(dependency, path, packages)
        for product in dependency.products or default_products(dependency, package):
            builder.add_product(package, product)

    return PackageGraph(
        targets=tuple(builder.ordered),
        package_roots=tuple(p.path for p in packages.values()),
    )


def _manifest_hashes(root: Path) -> dict[str, str]:
    return {p.name: sha256_file(p) for p in sorted(root.glob("Package*.swift")) if p.is_file()}


def _listing_hash(directory: Path) -> str:
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        for name in sorted(filenames):
            entries.append((Path(dirpath) / name).relative_to(directory).as_posix())
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _graph_key(
    dependencies: Sequence[tuple[Dependency, Path | None]],
    toolchain: Toolchain,
    deployment_target: str,
    lock: Path,
) -> str:
    return fingerprint(
        {
            "swift": toolchain.swift_version,
            "deployment_target": deployment_target,
            "dependencies": [
                [
                    d.name,
                    p.as_posix() if p is not None else None,
                    d.url,
                    [d.requirement.kind, d.requirement.value] if d.requirement else None,
                    list(d.products),
                ]
                for d, p in dependencies
            ],
            "lock": sha256_file(lock) if lock.is_file() else None,
        }
    )


def project_lock(layout: BuildLayout) -> Path:
    return layout.project / LOCK_FILE


# swiftpm only sees a root copy of the project lock
def _seed_lock(project: Path, root: Path) -> None:
    if project.is_file():
        write_atomic(root, project.read_bytes())
    elif root.exists():
        root.unlink()


def _publish_lock(root: Path, project: Path) -> None:
    if root.is_file():
        data = root.read_bytes()
        if not project.is_file() or project.read_bytes() != data:
            write_atomic(project, data)
            get_reporter().info(f"pinned package versions in {project.name}")
    elif project.is_file():
        # stale lock would pin removed packages
        project.unlink()


def _serialize(graph: PackageGraph, key: str) -> dict[str, Any]:
    return {
        "version": GRAPH_VERSION,
        "key": key,
        "manifests": {str(root): _manifest_hashes(root) for root in graph.package_roots},
        "listings": {str(t.directory): _listing_hash(t.directory) for t in graph.targets},
        "targets": [
            {
                "package": t.package,
                "name": t.name,
                "module": t.module,
                "directory": str(t.directory),
                "sources": [str(s) for s in t.sources],
                "dependencies": list(t.dependencies),
                "swift_version": t.swift_version,
                "package_name": t.package_name,
                "defines": list(t.defines),
                "upcoming_features": list(t.upcoming_features),
                "experimental_features": list(t.experimental_features),
                "frameworks": list(t.frameworks),
                "libraries": list(t.libraries),
                "resources": [[str(r.path), r.rule, r.localization] for r in t.resources],
                "bundle": t.bundle,
                "region": t.region,
            }
            for t in graph.targets
        ],
    }


def _deserialize(data: dict[str, Any]) -> PackageGraph:
    targets = tuple(
        PackageTarget(
            package=t["package"],
            name=t["name"],
            module=t["module"],
            directory=Path(t["directory"]),
            sources=tuple(Path(s) for s in t["sources"]),
            dependencies=tuple(t["dependencies"]),
            swift_version=t["swift_version"],
            package_name=t["package_name"],
            defines=tuple(t["defines"]),
            upcoming_features=tuple(t["upcoming_features"]),
            experimental_features=tuple(t["experimental_features"]),
            frameworks=tuple(t["frameworks"]),
            libraries=tuple(t["libraries"]),
            resources=tuple(
                PackageResource(Path(path), rule, localization)
                for path, rule, localization in t["resources"]
            ),
            bundle=t["bundle"],
            region=t["region"],
        )
        for t in data["targets"]
    )
    return PackageGraph(targets=targets, package_roots=tuple(Path(r) for r in data["manifests"]))


def _cached_graph(path: Path, key: str) -> PackageGraph | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != GRAPH_VERSION or data.get("key") != key:
        return None
    try:
        for root, hashes in data["manifests"].items():
            if _manifest_hashes(Path(root)) != hashes:
                return None
        for directory, listing in data["listings"].items():
            if not Path(directory).is_dir() or _listing_hash(Path(directory)) != listing:
                return None
        return _deserialize(data)
    except (KeyError, TypeError, AttributeError, ValueError):
        return None


def graph_path(layout: BuildLayout) -> Path:
    return packages_dir(layout) / "graph.json"


# identity is last url or path component, lowercased
def package_identity(dependency: Dependency) -> str:
    location = (dependency.url or dependency.path or "").strip().rstrip("/\\")
    last = re.split(r"[/\\:]", location)[-1]
    if last.lower().endswith(".git"):
        last = last[: -len(".git")]
    return last.lower()


def pinned_versions(layout: BuildLayout) -> dict[str, str]:
    lock = project_lock(layout)
    if not lock.is_file():
        return {}
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as err:
        raise PackageError(f"{lock} is not a readable lock file ({err})") from err
    pins: dict[str, str] = {}
    for pin in data.get("pins", []) if isinstance(data, dict) else []:
        identity = pin.get("identity") if isinstance(pin, dict) else None
        state = pin.get("state") if isinstance(pin, dict) else None
        if not isinstance(identity, str) or not isinstance(state, dict):
            continue
        if state.get("version"):
            pins[identity] = str(state["version"])
        elif state.get("branch"):
            pins[identity] = f"{state['branch']} at {str(state.get('revision', ''))[:7]}"
        elif state.get("revision"):
            pins[identity] = str(state["revision"])[:7]
    return pins


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    identity: str
    source: str
    rule: str
    pinned: str | None


def dependency_statuses(manifest: Manifest, layout: BuildLayout) -> list[DependencyStatus]:
    pins = pinned_versions(layout)
    statuses = []
    for dependency in manifest.dependencies:
        identity = package_identity(dependency)
        requirement = dependency.requirement
        statuses.append(
            DependencyStatus(
                name=dependency.name,
                identity=identity,
                source=dependency.url or dependency.path or "",
                rule=f"{requirement.kind} {requirement.value}" if requirement else "local path",
                pinned=pins.get(identity) if dependency.url is not None else None,
            )
        )
    return statuses


# names are iosc.toml keys or swiftpm identities
def update_identities(manifest: Manifest, layout: BuildLayout, names: Sequence[str]) -> list[str]:
    remote = [d for d in manifest.dependencies if d.url is not None]
    by_key = {d.name.lower(): package_identity(d) for d in remote}
    pinned = set(pinned_versions(layout))
    identities: list[str] = []
    for name in names:
        identity = by_key.get(name.lower(), name.lower())
        if identity not in pinned and identity not in by_key.values():
            indirect = pinned - set(by_key.values())
            known = ", ".join([d.name for d in remote] + sorted(indirect)) or "none"
            raise PackageError(f"no url package named {name}, the updatable packages are {known}")
        identities.append(identity)
    return list(dict.fromkeys(identities))


def load_graph(
    manifest: Manifest,
    layout: BuildLayout,
    toolchain: Toolchain,
    refresh: bool = False,
    update: Sequence[str] | None = None,
) -> PackageGraph:
    if not manifest.dependencies:
        if update is not None:
            raise PackageError("iosc.toml has no dependencies to update")
        return PackageGraph()

    dependencies = [(d, dependency_path(d, layout.project)) for d in manifest.dependencies]
    lock = project_lock(layout)
    key = _graph_key(dependencies, toolchain, manifest.deployment_target, lock)
    cache = graph_path(layout)
    cached = None if refresh or update is not None else _cached_graph(cache, key)
    if cached is not None:
        get_reporter().detail(f"package graph up to date, {len(cached.targets)} targets")
        return cached
    identities = update_identities(manifest, layout, update) if update else []

    if any(d.url is not None for d in manifest.dependencies):
        find_git()

    state = packages_dir(layout)
    root = state / "root"
    declarations = [package_declaration(d, p) for d, p in dependencies]
    write_atomic(root / "Package.swift", render_root_manifest(declarations).encode("utf-8"))
    _seed_lock(lock, root / LOCK_FILE)
    env = msvc.environment()

    if update is None:
        swiftpm.resolve(toolchain, root, state, env)
    else:
        swiftpm.update(toolchain, root, state, env, identities)
    _publish_lock(root / LOCK_FILE, lock)

    with Heartbeat("reading swift packages"):
        tree = swiftpm.show_dependencies(toolchain, root, state, env)
        flat: dict[str, _Node] = {}
        _flatten_tree(tree, flat)
        descriptions = {}
        manifests = {}
        for identity, node in flat.items():
            descriptions[identity] = swiftpm.describe(toolchain, node.path, state, env)
            manifests[identity] = swiftpm.dump_package(toolchain, node.path, state, env)

    graph = build_graph(dependencies, tree, descriptions, manifests, manifest.deployment_target)
    # rekey after resolve
    key = _graph_key(dependencies, toolchain, manifest.deployment_target, lock)
    text = json.dumps(_serialize(graph, key), indent=2, sort_keys=True) + "\n"
    write_atomic(cache, text.encode("utf-8"))
    get_reporter().detail(f"package graph has {len(graph.targets)} targets")
    return graph


# generated swift sources, keyed by path
def derived_sources(target: PackageTarget, layout: BuildLayout) -> dict[Path, Callable[[], str]]:
    directory = derived_dir(layout, target.module)
    derived: dict[Path, Callable[[], str]] = {}
    if target.bundle is not None:
        name = target.bundle
        derived[directory / package_resources.ACCESSOR_NAME] = (
            lambda: package_resources.accessor_source(name)
        )
    embedded = package_resources.embedded(target.resources)
    if embedded:
        derived[directory / package_resources.EMBEDDED_NAME] = (
            lambda: package_resources.embedded_source(embedded)
        )
    return derived


def compile_spec(
    target: PackageTarget, layout: BuildLayout, sdk_root: Path, deployment_target: str
) -> CompileSpec:
    modules = module_dir(layout)
    return CompileSpec(
        sources=[*target.sources, *derived_sources(target, layout)],
        output=object_path(layout, target.module),
        sdk_root=sdk_root,
        deployment_target=deployment_target,
        module_name=target.module,
        package_name=target.package_name,
        swift_version=target.swift_version,
        parse_as_library=True,
        defines=[*PACKAGE_DEFINES, *target.defines],
        upcoming_features=list(target.upcoming_features),
        experimental_features=list(target.experimental_features),
        include_dirs=[modules],
        emit_module=modules / f"{target.module}.swiftmodule",
    )


def package_stages(
    graph: PackageGraph,
    layout: BuildLayout,
    toolchain: Toolchain,
    sdk_root: Path,
    deployment_target: str,
) -> list[Stage]:
    stages = []
    for target in graph.targets:
        spec = compile_spec(target, layout, sdk_root, deployment_target)
        derived = derived_sources(target, layout)
        embedded = [r.path for r in package_resources.embedded(target.resources)]
        print_ = fingerprint(
            {
                "swift": toolchain.swift_version,
                "target": f"arm64-apple-ios{deployment_target}",
                "sdk": sdk_root.name,
                "module": target.module,
                "package_name": target.package_name,
                "swift_version": target.swift_version,
                "defines": list(spec.defines),
                "upcoming": list(target.upcoming_features),
                "experimental": list(target.experimental_features),
                "sources": [relative_key(p, layout.project) for p in target.sources],
                "bundle": target.bundle,
                "embedded": [relative_key(p, layout.project) for p in embedded],
                "plugins": sorted((p.module, p.sha256) for p in toolchain.macro_plugins),
            }
        )

        def run(
            spec: CompileSpec = spec,
            target: PackageTarget = target,
            derived: dict[Path, Callable[[], str]] = derived,
        ) -> None:
            for path, render in derived.items():
                write_atomic(path, render().encode("utf-8"))
            with Heartbeat(f"compiling {target.module} from {target.package}"):
                compile_objects(toolchain, spec)

        stages.append(
            Stage(
                name=stage_name(target.module),
                run=run,
                fingerprint=print_,
                depends_on=tuple(stage_name(m) for m in target.dependencies),
                inputs=(*target.sources, *embedded),
                outputs=(spec.output, spec.emit_module, *derived),
            )
        )
        if target.bundle is not None:
            stages.append(resources_stage(target, layout, deployment_target))
    return stages


def resource_bundles(graph: PackageGraph, layout: BuildLayout) -> list[Path]:
    return [bundle_path(layout, t.bundle) for t in graph.targets if t.bundle is not None]


def resource_stage_names(graph: PackageGraph) -> list[str]:
    return [resources_stage_name(t.module) for t in graph.targets if t.bundle is not None]


def resources_stage(target: PackageTarget, layout: BuildLayout, deployment_target: str) -> Stage:
    if target.bundle is None:
        raise PackageError(f"package {target.package} target {target.name} has no resource bundle")
    bundle = bundle_path(layout, target.bundle)
    produced_path = bundle.with_name(f"{bundle.name}.outputs.json")
    bundled = package_resources.bundled(target.resources)
    inputs = package_resources.resource_files(bundled)

    print_ = fingerprint(
        {
            "bundle": target.bundle,
            "region": target.region,
            "deployment_target": deployment_target,
            "resources": [
                [relative_key(r.path, layout.project), r.rule, r.localization] for r in bundled
            ],
            "files": [relative_key(p, layout.project) for p in inputs],
        }
    )

    def run() -> None:
        produced = package_resources.assemble_bundle(
            bundled, bundle, target.region, deployment_target
        )
        write_output_manifest(produced_path, layout.project, produced)
        get_reporter().detail(f"bundled {len(bundled)} resources into {bundle.name}")

    return Stage(
        name=resources_stage_name(target.module),
        run=run,
        fingerprint=print_,
        inputs=tuple(inputs),
        resolve_outputs=lambda: (produced_path, *read_output_manifest(produced_path, layout.project)),
    )
