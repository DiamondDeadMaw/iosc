from collections.abc import Sequence
from pathlib import Path

from iosc.core import BundleError, ensure_dir, get_reporter, write_atomic
from iosc.core.errors import MachOError
from iosc.formats import macho

RPATH_PREFIX = "@rpath/"
SWIFT_PREFIX = "libswift"
DYLIB_SUFFIX = ".dylib"
FRAMEWORKS_DIR = "Frameworks"


def rpath_swift_libraries(data: bytes) -> list[str]:
    names = []
    for load in macho.dylib_loads(data):
        name = load[len(RPATH_PREFIX) :] if load.startswith(RPATH_PREFIX) else ""
        if name.startswith(SWIFT_PREFIX) and name.endswith(DYLIB_SUFFIX) and "/" not in name:
            names.append(name)
    return names


def _find(name: str, search_dirs: Sequence[Path]) -> Path | None:
    for directory in search_dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


# swift-stdlib-tool equivalent, walks transitive loads
def embed_swift_libraries(executable: Path, app: Path, search_dirs: Sequence[Path]) -> list[Path]:
    try:
        pending = rpath_swift_libraries(executable.read_bytes())
    except MachOError as err:
        raise BundleError(f"{executable} {err}") from err
    frameworks = app / FRAMEWORKS_DIR
    embedded: list[Path] = []
    seen: set[str] = set()
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        source = _find(name, search_dirs)
        if source is None:
            looked = ", ".join(str(d) for d in search_dirs) or "no Xcode toolchain under external/xcode"
            raise BundleError(
                f"{executable.name} loads @rpath/{name}, a Swift back deployment library Xcode "
                f"copies from its toolchain, and none was found (looked in {looked}). "
                f"Raising deployment_target to an iOS that ships it also removes the need"
            )
        try:
            thin = macho.thin_arm64(source.read_bytes())
        except MachOError as err:
            raise BundleError(f"{source} {err}") from err
        target = ensure_dir(frameworks) / name
        write_atomic(target, thin)
        embedded.append(target)
        pending.extend(rpath_swift_libraries(thin))
    for target in embedded:
        get_reporter().detail(f"embedded {target.name} for deployment below the iOS that ships it")
    return embedded
