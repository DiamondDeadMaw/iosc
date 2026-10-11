from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from collections.abc import Sequence

from iosc.core import CompileError, CompletedProcess, ExternalToolError, process
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain.swift import target_triple

# language per source suffix, assembly is not built yet
LANGUAGES = {
    ".c": "c",
    ".m": "objc",
    ".cc": "cxx",
    ".cpp": "cxx",
    ".cxx": "cxx",
    ".c++": "cxx",
    ".mm": "objcxx",
}
ARC_LANGUAGES = ("objc", "objcxx")
CXX_LANGUAGES = ("cxx", "objcxx")
# flags that cannot change the target, the sysroot or what gets linked
SAFE_FLAG = re.compile(
    r"^(-w|-W(?![lap],)[\w=+-]+|-f(?!plugin|pass-plugin|use-ld)(no-)?[\w=+-]+"
    r"|-O[0-3sz]?|-g[\w-]*|-[DUI]\S+|-std=\S+)$"
)
SAFE_FLAG_SUMMARY = "-W, -w, -f, -O, -g, -D, -U, -I and -std flags"
DEPFILE_HEAD = re.compile(r"^.*?:\s+", re.DOTALL)
DEPFILE_PATH = re.compile(r"(?:\\ |[^\s])+")


@dataclass(frozen=True)
class ClangSpec:
    source: Path
    output: Path
    depfile: Path
    sdk_root: Path
    deployment_target: str
    language: str
    arch: str = "arm64"
    standard: str | None = None
    flags: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    framework_dirs: list[Path] = field(default_factory=list)
    simulator: bool = False


def language_of(source: Path) -> str | None:
    return LANGUAGES.get(source.suffix)


# first flag outside the accepted set, None when all pass
def unsafe_flag(flags: Sequence[str]) -> str | None:
    return next((f for f in flags if not SAFE_FLAG.match(f)), None)


def build_clang_argv(toolchain: Toolchain, spec: ClangSpec) -> list[str]:
    argv = [
        str(toolchain.clang),
        "-c",
        "-target",
        target_triple(spec.arch, spec.deployment_target, spec.simulator),
        "-isysroot",
        str(spec.sdk_root),
        "-g",
        "-O0",
        "-fblocks",
    ]
    if spec.language in ARC_LANGUAGES:
        argv.append("-fobjc-arc")
    if spec.standard:
        argv.append(f"-std={spec.standard}")
    for define in spec.defines:
        argv.append(f"-D{define}")
    for include in spec.include_dirs:
        argv.extend(["-I", str(include)])
    for directory in spec.framework_dirs:
        argv.extend(["-F", str(directory)])
    # package flags come last so a package can override the defaults above
    argv.extend(spec.flags)
    argv.extend(["-MMD", "-MF", str(spec.depfile), "-o", str(spec.output), str(spec.source)])
    return argv


def compile_source(toolchain: Toolchain, spec: ClangSpec) -> CompletedProcess:
    spec.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        return process.run(build_clang_argv(toolchain, spec))
    except ExternalToolError as err:
        raise CompileError(err.stderr if err.stderr else str(err)) from err


def compile_sources(toolchain: Toolchain, specs: Sequence[ClangSpec]) -> None:
    with ThreadPoolExecutor(max_workers=min(len(specs), os.cpu_count() or 1) or 1) as pool:
        for future in [pool.submit(compile_source, toolchain, spec) for spec in specs]:
            future.result()


# headers a depfile names, make syntax with escaped spaces and continuations
def parse_depfile(text: str) -> list[Path]:
    body = DEPFILE_HEAD.sub("", text.replace("\\\r\n", " ").replace("\\\n", " "), count=1)
    return [Path(m.group().replace("\\ ", " ")) for m in DEPFILE_PATH.finditer(body)]
