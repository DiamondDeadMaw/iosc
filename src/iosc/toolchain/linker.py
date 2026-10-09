from dataclasses import dataclass, field
from pathlib import Path

from iosc.core import CompletedProcess, ExternalToolError, LinkError, process
from iosc.toolchain.discovery import Toolchain


@dataclass(frozen=True)
class LinkSpec:
    objects: list[Path]
    output: Path
    sdk_root: Path
    deployment_target: str
    arch: str = "arm64"
    frameworks: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=lambda: ["System", "objc"])
    # swift emits calls to builtins like ___isPlatformVersionAtLeast
    # nothing in the sdk defines them
    clang_rt: Path | None = None
    rpaths: list[str] = field(default_factory=list)
    extra_flags: list[str] = field(default_factory=list)


def build_link_argv(toolchain: Toolchain, spec: LinkSpec) -> list[str]:
    argv = [
        str(toolchain.linker),
        "-arch",
        spec.arch,
        "-platform_version",
        "ios",
        spec.deployment_target,
        spec.deployment_target,
        "-syslibroot",
        str(spec.sdk_root),
        "-L",
        str(spec.sdk_root / "usr" / "lib" / "swift"),
    ]
    if spec.frameworks:
        argv.extend(["-F", str(spec.sdk_root / "System" / "Library" / "Frameworks")])
    for lib in spec.libraries:
        argv.append(lib if lib.startswith("-l") else f"-l{lib}")
    for fw in spec.frameworks:
        argv.extend(["-framework", fw])
    if spec.clang_rt is not None:
        argv.append(str(spec.clang_rt))
    for rpath in spec.rpaths:
        argv.extend(["-rpath", rpath])
    for flag in spec.extra_flags:
        argv.append(flag)
    argv.extend(["-o", str(spec.output)])
    for obj in spec.objects:
        argv.append(str(obj))
    return argv


def link(toolchain: Toolchain, spec: LinkSpec) -> CompletedProcess:
    spec.output.parent.mkdir(parents=True, exist_ok=True)
    argv = build_link_argv(toolchain, spec)
    try:
        return process.run(argv)
    except ExternalToolError as err:
        msg = err.stderr if err.stderr else str(err)
        raise LinkError(msg) from err
