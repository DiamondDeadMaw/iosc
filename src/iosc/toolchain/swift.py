from dataclasses import dataclass, field
from pathlib import Path

from iosc.core import CompileError, CompletedProcess, ExternalToolError, process
from iosc.toolchain.discovery import Toolchain


@dataclass(frozen=True)
class CompileSpec:
    sources: list[Path]
    output: Path
    sdk_root: Path
    deployment_target: str
    arch: str = "arm64"
    extra_flags: list[str] = field(default_factory=list)
    module_name: str | None = None
    package_name: str | None = None
    swift_version: str | None = None
    parse_as_library: bool = False
    defines: list[str] = field(default_factory=list)
    upcoming_features: list[str] = field(default_factory=list)
    experimental_features: list[str] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    emit_module: Path | None = None


def _module_flags(spec: CompileSpec) -> list[str]:
    argv: list[str] = []
    if spec.module_name:
        argv.extend(["-module-name", spec.module_name])
    if spec.package_name:
        argv.extend(["-package-name", spec.package_name])
    if spec.swift_version:
        argv.extend(["-swift-version", spec.swift_version])
    if spec.parse_as_library:
        argv.append("-parse-as-library")
    for define in spec.defines:
        argv.extend(["-D", define])
    for feature in spec.upcoming_features:
        argv.extend(["-enable-upcoming-feature", feature])
    for feature in spec.experimental_features:
        argv.extend(["-enable-experimental-feature", feature])
    for include in spec.include_dirs:
        argv.extend(["-I", str(include)])
    if spec.emit_module is not None:
        argv.extend(["-emit-module", "-emit-module-path", str(spec.emit_module)])
    return argv


def build_compile_argv(toolchain: Toolchain, spec: CompileSpec) -> list[str]:
    argv = [
        str(toolchain.swiftc),
        "-c",
        "-target",
        f"{spec.arch}-apple-ios{spec.deployment_target}",
        "-sdk",
        str(spec.sdk_root),
        "-g",
        # cross-import overlays are off by default in this swift build, xcode has them on
        # they add members that appear when two bridged frameworks meet
        # e.g. SwiftData and SwiftUI for @Query
        "-Xfrontend",
        "-enable-cross-import-overlays",
        # assert builds abort on some sdk types
        "-Xfrontend",
        "-disable-round-trip-debug-types",
    ]
    # swiftc loads a plugin process only when a macro resolves to its module
    for plugin in toolchain.macro_plugins:
        argv.extend(["-load-plugin-executable", f"{plugin.path}#{plugin.module}"])
    argv.extend(_module_flags(spec))
    # swift resolves names across the whole module, one -o wants one object
    # compile multiple sources whole-module
    if len(spec.sources) > 1:
        argv.append("-wmo")
    for src in spec.sources:
        argv.append(str(src))
    for flag in spec.extra_flags:
        argv.append(flag)
    argv.extend(["-o", str(spec.output)])
    return argv


def compile_objects(toolchain: Toolchain, spec: CompileSpec) -> CompletedProcess:
    spec.output.parent.mkdir(parents=True, exist_ok=True)
    if spec.emit_module is not None:
        spec.emit_module.parent.mkdir(parents=True, exist_ok=True)
    argv = build_compile_argv(toolchain, spec)
    try:
        return process.run(argv)
    except ExternalToolError as err:
        msg = err.stderr if err.stderr else str(err)
        raise CompileError(msg) from err
