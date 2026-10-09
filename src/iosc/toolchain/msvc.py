from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from iosc.config import paths
from iosc.core import ExternalToolError, ToolchainError, get_reporter, process, write_atomic

CACHE_VERSION = 1
VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
VCVARSALL = Path("VC") / "Auxiliary" / "Build" / "vcvarsall.bat"
EDITIONS = ("BuildTools", "Community", "Professional", "Enterprise")
# year releases mapped onto the numbered rank scale
RELEASE_RANK = {"2017": 15, "2019": 16, "2022": 17}

BUILD_TOOLS_HINT = (
    "Swift packages need Visual Studio Build Tools with the C++ workload and the "
    "Windows SDK. Get them from https://visualstudio.microsoft.com/downloads/"
)


def _program_files() -> list[Path]:
    roots = []
    for var, default in (
        ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ("ProgramFiles", r"C:\Program Files"),
    ):
        root = Path(os.environ.get(var, default)) / "Microsoft Visual Studio"
        if root not in roots:
            roots.append(root)
    return roots


def _from_vswhere() -> Path | None:
    vswhere = _program_files()[0] / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    try:
        proc = process.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                VC_TOOLS_COMPONENT,
                "-property",
                "installationPath",
            ]
        )
    except ExternalToolError:
        return None
    for line in proc.stdout.splitlines():
        if line.strip():
            candidate = Path(line.strip()) / VCVARSALL
            if candidate.is_file():
                return candidate
    return None


def _release_rank(release: Path) -> int:
    if release.name in RELEASE_RANK:
        return RELEASE_RANK[release.name]
    return int(release.name) if release.name.isdigit() else 0


def _from_default_folders() -> Path | None:
    releases = [
        release
        for root in _program_files()
        if root.is_dir()
        for release in root.iterdir()
        if release.is_dir()
    ]
    for release in sorted(releases, key=_release_rank, reverse=True):
        for edition in EDITIONS:
            candidate = release / edition / VCVARSALL
            if candidate.is_file():
                return candidate
    return None


def find_vcvarsall() -> Path:
    found = _from_vswhere() or _from_default_folders()
    if found is None:
        raise ToolchainError(f"vcvarsall.bat not found. {BUILD_TOOLS_HINT}")
    return found


def parse_set_output(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            env[key.upper()] = value
    return env


# keep only what vcvarsall prepended
def environment_delta(
    captured: Mapping[str, str], base: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    current = {k.upper(): v for k, v in base.items()}
    prepend: dict[str, str] = {}
    assign: dict[str, str] = {}
    for key, value in captured.items():
        before = current.get(key.upper())
        if before == value:
            continue
        if before and value.endswith(before):
            prepend[key.upper()] = value[: len(value) - len(before)]
        else:
            assign[key.upper()] = value
    return prepend, assign


def apply_delta(
    prepend: Mapping[str, str], assign: Mapping[str, str], base: Mapping[str, str]
) -> dict[str, str]:
    current = {k.upper(): v for k, v in base.items()}
    env = {key: fragment + current.get(key, "") for key, fragment in prepend.items()}
    env.update(assign)
    return env


def _capture(vcvarsall: Path) -> dict[str, str]:
    comspec = os.environ.get("ComSpec", "cmd.exe")
    with tempfile.TemporaryDirectory(prefix="iosc-msvc-") as tmp:
        script = Path(tmp) / "capture.cmd"
        # utf-8 code page before set, else output is oem
        script.write_text(
            "@chcp 65001 >nul\r\n"
            f'@call "{vcvarsall}" x64 >nul 2>&1\r\n'
            "@if errorlevel 1 exit /b 1\r\n"
            "@set\r\n",
            encoding="utf-8",
        )
        try:
            proc = process.run([comspec, "/d", "/c", str(script)])
        except ExternalToolError as err:
            raise ToolchainError(
                f"{vcvarsall} x64 failed with exit code {err.exit_code}. {BUILD_TOOLS_HINT}"
            ) from err
    captured = parse_set_output(proc.stdout)
    if "INCLUDE" not in captured:
        raise ToolchainError(
            f"{vcvarsall} x64 set no INCLUDE, the C++ workload looks incomplete. {BUILD_TOOLS_HINT}"
        )
    return captured


def _load_cache(path: Path, vcvarsall: Path, mtime_ns: int) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if (
        data.get("version") != CACHE_VERSION
        or data.get("vcvarsall") != str(vcvarsall)
        or data.get("mtime_ns") != mtime_ns
        or not isinstance(data.get("prepend"), dict)
        or not isinstance(data.get("assign"), dict)
    ):
        return None
    return data


# env swiftpm needs to compile manifests on windows
def environment(cache_path: Path | None = None) -> dict[str, str]:
    if os.name != "nt":
        return {}
    cache = cache_path if cache_path is not None else paths.msvc_environment_cache()
    vcvarsall = find_vcvarsall()
    mtime_ns = vcvarsall.stat().st_mtime_ns

    data = _load_cache(cache, vcvarsall, mtime_ns)
    if data is None:
        get_reporter().detail(f"capturing msvc environment from {vcvarsall}")
        prepend, assign = environment_delta(_capture(vcvarsall), os.environ)
        data = {
            "version": CACHE_VERSION,
            "vcvarsall": str(vcvarsall),
            "mtime_ns": mtime_ns,
            "prepend": prepend,
            "assign": assign,
        }
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        write_atomic(cache, text.encode("utf-8"))

    return apply_delta(data["prepend"], data["assign"], os.environ)
