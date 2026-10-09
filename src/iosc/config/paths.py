from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys

from iosc.core import MissingExternalAsset


@dataclass(frozen=True)
class ExternalAsset:
    name: str
    path: Path
    hint: str


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _resolve_external_root() -> tuple[Path, str]:
    override = os.environ.get("IOSC_EXTERNAL")
    if override:
        return Path(override), "IOSC_EXTERNAL"
    # a single-file exe has no source tree
    # prefer an external dir beside the exe, fall back to user state.
    # resolved once per process and always created up front so this
    # cannot flip between the two across calls within a session
    if is_frozen():
        beside = Path(sys.executable).resolve().parent / "external"
        if beside.is_dir():
            return beside, "beside iosc.exe"
        try:
            beside.mkdir(parents=True, exist_ok=True)
        except OSError:
            return state_dir() / "external", "user state"
        return beside, "beside iosc.exe"
    pkg_dir = Path(__file__).resolve().parent.parent
    if pkg_dir.parent.name == "src":
        return pkg_dir.parent.parent / "external", "source checkout"
    # installed, not run from source. external lives beside user state
    return state_dir() / "external", "user state"


def external_root() -> Path:
    return _resolve_external_root()[0]


def external_root_source() -> str:
    return _resolve_external_root()[1]


def state_dir() -> Path:
    override = os.environ.get("IOSC_STATE")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "iosc"
    return Path.home() / "AppData" / "Local" / "iosc"


def cache_dir() -> Path:
    return state_dir() / "cache"


def msvc_environment_cache() -> Path:
    return cache_dir() / "msvc-environment.json"


def xcode_xip_dir() -> ExternalAsset:
    return ExternalAsset(
        name="xcode_xip_dir",
        path=external_root() / "xcode",
        hint="Place Xcode.xip in external/xcode or run iosc sdk extract.",
    )


# where the xip carve lands, mirroring the archive namespace
def sdk_extract_root() -> ExternalAsset:
    return ExternalAsset(
        name="sdk_extract_root",
        path=external_root() / "xcode",
        hint="Run 'iosc sdk extract <xip>' to materialize the toolchain tree.",
    )


def sdk_root() -> ExternalAsset:
    return ExternalAsset(
        name="sdk_root",
        path=(
            external_root()
            / "xcode"
            / "Platforms"
            / "iPhoneOS.platform"
            / "Developer"
            / "SDKs"
            / "iPhoneOS.sdk"
        ),
        hint="Run 'iosc sdk extract <xip>' to extract the iOS SDK.",
    )


# public apple CA files. user places them here per README
# iosc never fetches them
APPLE_INTERMEDIATES = ("AppleWWDRCAG3.cer", "AppleIncRootCertificate.cer")


def apple_certs_dir() -> ExternalAsset:
    return ExternalAsset(
        name="apple_certs_dir",
        path=external_root() / "apple_certs",
        hint=(
            "Place "
            + " and ".join(APPLE_INTERMEDIATES)
            + " in external/apple_certs. README.md names where Apple publishes them."
        ),
    )


def apple_intermediates() -> tuple[str, ...]:
    return APPLE_INTERMEDIATES


def apk_dir() -> ExternalAsset:
    return ExternalAsset(
        name="apk_dir",
        path=external_root() / "apk",
        hint="Place the Apple Music APK in external/apk.",
    )


def adi_lib_dir() -> ExternalAsset:
    return ExternalAsset(
        name="adi_lib_dir",
        path=external_root() / "apk" / "adi",
        hint="Extract ADI libraries into external/apk/adi.",
    )


def tools_dir() -> ExternalAsset:
    return ExternalAsset(
        name="tools_dir",
        path=external_root() / "tools",
        hint="Place required third party binaries in external/tools.",
    )


def ld64_lld() -> ExternalAsset:
    return ExternalAsset(
        name="ld64_lld",
        path=external_root() / "tools" / "ld64.lld.exe",
        hint=(
            "Place an upstream LLVM ld64.lld.exe in external/tools. "
            "The copy bundled with the Swift toolchain rejects iOS platform inputs."
        ),
    )


def dsymutil_tool() -> ExternalAsset:
    return ExternalAsset(
        name="dsymutil_tool",
        path=external_root() / "tools" / "dsymutil.exe",
        hint="Place dsymutil.exe in external/tools, or run 'iosc toolchain fetch'.",
    )


def llvm_symbolizer_tool() -> ExternalAsset:
    return ExternalAsset(
        name="llvm_symbolizer_tool",
        path=external_root() / "tools" / "llvm-symbolizer.exe",
        hint="Place llvm-symbolizer.exe in external/tools, or run 'iosc toolchain fetch'.",
    )


def adi_bridge() -> ExternalAsset:
    return ExternalAsset(
        name="adi_bridge",
        path=vendor_adi_dir() / "adi_native.dll",
        hint=(
            "adi_native.dll ships inside iosc.exe and should already be here. "
            "If you built iosc from source yourself, run packaging/build.ps1, "
            "which compiles it from native/adi before packaging."
        ),
    )


ADI_REQUIRED_LIBRARIES = (
    "libstoreapi.so",
    "libCoreADI.so",
    "libCoreFP.so",
    "libCoreLSKD.so",
    "libFPDIFor3P.so",
)


def adi_libraries() -> tuple[str, ...]:
    return ADI_REQUIRED_LIBRARIES


def require(asset: ExternalAsset) -> Path:
    if not asset.path.exists():
        raise MissingExternalAsset(f"{asset.hint} (looked at {asset.path})")
    return asset.path


def exists(asset: ExternalAsset) -> bool:
    return asset.path.exists()


# ios compiler-rt from the user's extracted xcode
# swift needs builtins like ___isPlatformVersionAtLeast that the sdk lacks
# clang version dir floats, take the newest
def clang_rt_ios() -> Path | None:
    clang_root = (
        external_root() / "xcode" / "Toolchains" / "XcodeDefault.xctoolchain"
        / "usr" / "lib" / "clang"
    )
    if not clang_root.is_dir():
        return None
    candidates = sorted(clang_root.glob("*/lib/darwin/libclang_rt.ios.a"))
    return candidates[-1] if candidates else None


# swift libs copied into apps deployed below their os
# newest swift first, first match wins
def swift_backdeploy_dirs() -> list[Path]:
    lib_root = (
        external_root() / "xcode" / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "lib"
    )
    if not lib_root.is_dir():
        return []
    found = [p for p in lib_root.glob("swift-*/iphoneos") if p.is_dir()]
    return sorted(found, key=lambda p: _version_key(p.parent.name), reverse=True)


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name))


# the seven macro plugins are built from product/macros
# they live beside vendor, not external
def macro_plugins_dir() -> Path:
    override = os.environ.get("IOSC_MACRO_PLUGINS")
    if override:
        return Path(override)
    pkg_dir = Path(__file__).resolve().parent.parent
    if pkg_dir.parent.name == "src":
        return pkg_dir.parent.parent / "macros" / "bin"
    return pkg_dir / "macros" / "bin"


def vendor_lzfse_dir() -> Path:
    override = os.environ.get("IOSC_VENDOR_LZFSE")
    if override:
        return Path(override)
    pkg_dir = Path(__file__).resolve().parent.parent
    if pkg_dir.parent.name == "src":
        return pkg_dir.parent.parent / "vendor" / "lzfse"
    return pkg_dir / "vendor" / "lzfse"


def vendor_adi_dir() -> Path:
    override = os.environ.get("IOSC_VENDOR_ADI")
    if override:
        return Path(override)
    pkg_dir = Path(__file__).resolve().parent.parent
    if pkg_dir.parent.name == "src":
        return pkg_dir.parent.parent / "vendor" / "adi"
    return pkg_dir / "vendor" / "adi"


# platform trees the sdk carve keeps
CARVE_PLATFORMS = ("iPhoneOS", "iPhoneSimulator", "MacOSX")


def carve_platforms() -> tuple[str, ...]:
    return CARVE_PLATFORMS
