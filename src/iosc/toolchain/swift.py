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
    ]
    # swiftc loads a plugin process only when a macro resolves to its module
    for plugin in toolchain.macro_plugins:
        argv.extend(["-load-plugin-executable", f"{plugin.path}#{plugin.module}"])
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
    argv = build_compile_argv(toolchain, spec)
    try:
        return process.run(argv)
    except ExternalToolError as err:
        msg = err.stderr if err.stderr else str(err)
        raise CompileError(msg) from err
