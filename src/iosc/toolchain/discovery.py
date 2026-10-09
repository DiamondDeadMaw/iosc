from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil

from iosc.config import paths
from iosc.config.settings import Settings
from iosc.core import MissingExternalAsset, ToolchainError, process

PLUGIN_MANIFEST = "plugins.json"

# apple builds these as arm64 macos binaries. product/macros reimplements each for windows
# module name is what #externalMacro resolves
MACRO_PLUGIN_MODULES = (
    "SwiftUIMacros",
    "SwiftDataMacros",
    "PreviewsMacros",
    "FoundationModelsMacros",
    "StateReportingMacros",
    "TipKitMacros",
    "AppIntentsMacros",
)

_BUILD_HINT = "Run product/macros/build.ps1 to build and install the macro plugins."


@dataclass(frozen=True)
class MacroPlugin:
    module: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class Toolchain:
    swift_bin_dir: Path
    swiftc: Path
    linker: Path
    swift_version: str
    macro_plugins: tuple[MacroPlugin, ...] = field(default_factory=tuple)


_SWIFT_VERSION_CACHE: dict[Path, str] = {}


def _resolve_candidate(candidate: Path | str) -> Path | None:
    p = Path(candidate)
    if p.is_file():
        return p
    if p.is_dir():
        for name in ("swiftc.exe", "swiftc"):
            if (p / name).is_file():
                return p / name
            if (p / "bin" / name).is_file():
                return p / "bin" / name
            if (p / "usr" / "bin" / name).is_file():
                return p / "usr" / "bin" / name
    return None


def find_swiftc(settings: Settings | None = None) -> Path:
    if settings is not None and settings.swift_toolchain_path:
        found = _resolve_candidate(settings.swift_toolchain_path)
        if found is not None:
            return found.resolve()

    env_swift = os.environ.get("IOSC_SWIFT")
    if env_swift:
        found = _resolve_candidate(env_swift)
        if found is not None:
            return found.resolve()

    which_swift = shutil.which("swiftc")
    if which_swift:
        return Path(which_swift).resolve()

    searched = [
        f"settings.swift_toolchain_path ({settings.swift_toolchain_path if settings else None})",
        f"IOSC_SWIFT ({env_swift})",
        "shutil.which('swiftc')",
    ]
    raise ToolchainError(f"Could not find swiftc. Searched {', '.join(searched)}")


def find_git() -> Path:
    found = shutil.which("git")
    if not found:
        raise ToolchainError(
            "git not found on PATH. Swift packages from a url need it, "
            "get it from https://git-scm.com/download/win"
        )
    return Path(found)


def find_linker(swift_bin_dir: Path | None = None) -> Path:
    try:
        linker = paths.require(paths.ld64_lld())
    except MissingExternalAsset as err:
        msg = str(err)
        if "iosc toolchain fetch" not in msg:
            msg = f"{msg} Run 'iosc toolchain fetch' to acquire it."
        raise MissingExternalAsset(msg) from err

    if swift_bin_dir is None:
        try:
            swiftc = find_swiftc()
            swift_bin_dir = swiftc.parent
        except Exception:
            swift_bin_dir = None

    if swift_bin_dir is not None:
        resolved_linker = linker.resolve()
        resolved_bin = swift_bin_dir.resolve()
        toolchain_root = resolved_bin
        if resolved_bin.name.lower() == "bin":
            if resolved_bin.parent.name.lower() == "usr":
                toolchain_root = resolved_bin.parent.parent
            else:
                toolchain_root = resolved_bin.parent

        is_inside = False
        try:
            if resolved_linker.is_relative_to(toolchain_root):
                is_inside = True
        except (ValueError, OSError):
            pass

        if not is_inside:
            try:
                if resolved_linker.is_relative_to(resolved_bin):
                    is_inside = True
            except (ValueError, OSError):
                pass

        if is_inside:
            raise ToolchainError(
                f"Linker at '{linker}' resolves inside Swift toolchain directory '{toolchain_root}'. "
                "The ld64.lld bundled with the Swift toolchain rejects iOS inputs with "
                "'This version of lld does not support linking for platform iOS'. "
                "Only an upstream LLVM build works. Run 'iosc toolchain fetch' to acquire it."
            )

    return linker


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_macro_plugins(plugin_dir: Path | None = None) -> tuple[MacroPlugin, ...]:
    root = plugin_dir if plugin_dir is not None else paths.macro_plugins_dir()
    manifest_path = root / PLUGIN_MANIFEST
    if not manifest_path.is_file():
        raise ToolchainError(
            f"Macro plugin manifest missing at '{manifest_path}'. {_BUILD_HINT}"
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as err:
        raise ToolchainError(
            f"Macro plugin manifest '{manifest_path}' is unreadable: {err}. {_BUILD_HINT}"
        ) from err

    recorded = {}
    for entry in manifest.get("plugins", []):
        module = entry.get("module")
        if module:
            recorded[module] = entry

    missing = [m for m in MACRO_PLUGIN_MODULES if m not in recorded]
    if missing:
        raise ToolchainError(
            f"Macro plugin manifest '{manifest_path}' does not list {', '.join(missing)}. "
            f"{_BUILD_HINT}"
        )

    plugins = []
    for module in MACRO_PLUGIN_MODULES:
        entry = recorded[module]
        path = root / entry.get("file", f"{module}-tool.exe")
        if not path.is_file():
            raise ToolchainError(
                f"Macro plugin '{module}' is listed in the manifest but missing at "
                f"'{path}'. {_BUILD_HINT}"
            )
        # a plugin the compiler cant load only surfaces as 'external macro implementation not found'
        # catch a stale binary here instead
        actual = _file_sha256(path)
        expected = str(entry.get("sha256", "")).lower()
        if actual != expected:
            raise ToolchainError(
                f"Macro plugin '{module}' at '{path}' does not match the manifest hash "
                f"(recorded {expected or 'nothing'}, found {actual}). {_BUILD_HINT}"
            )
        plugins.append(MacroPlugin(module=module, path=path, sha256=actual))

    return tuple(plugins)


def detect(settings: Settings | None = None) -> Toolchain:
    swiftc = find_swiftc(settings)
    swift_bin_dir = swiftc.parent
    linker = find_linker(swift_bin_dir)

    resolved_swiftc = swiftc.resolve()
    if resolved_swiftc in _SWIFT_VERSION_CACHE:
        swift_version = _SWIFT_VERSION_CACHE[resolved_swiftc]
    else:
        proc = process.run([str(swiftc), "--version"])
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        swift_version = lines[0] if lines else proc.stdout.strip()
        _SWIFT_VERSION_CACHE[resolved_swiftc] = swift_version

    return Toolchain(
        swift_bin_dir=swift_bin_dir,
        swiftc=swiftc,
        linker=linker,
        swift_version=swift_version,
        macro_plugins=find_macro_plugins(),
    )
