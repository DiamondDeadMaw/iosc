from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Any
import urllib.request
import zipfile

from iosc.core import PackageError, Progress, get_reporter, rmtree_force, sha256_file
from iosc.core.errors import MachOError
from iosc.formats import macho
from iosc.formats.plist import read_plist

ARCH = "arm64"
PLATFORM = "ios"
SIMULATOR = "simulator"
SUFFIX = ".xcframework"
INFO_PLIST = "Info.plist"
URL_SCHEMES = ("https", "file")
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 60
RETRY_DELAY = 2.0
# swiftpm names a cached zip after its url, every non alphanumeric character becomes an underscore
ARTIFACTS_DIR = "artifacts"
UNPACKED_DIR = "binaries"
RE_URL_SEPARATORS = re.compile(r"[^A-Za-z0-9]")
STATIC_MAGIC = b"!<arch>\n"
# files a swift compile reads from a slice
INTERFACE_DIRS = ("Headers", "Modules")
DEFAULT_MODULE_MAP = "module.modulemap"
FRAMEWORK = "framework"
LIBRARY = "library"


@dataclass(frozen=True)
class Slice:
    identifier: str
    platform: str
    variant: str | None
    architectures: tuple[str, ...]
    library: str
    headers: str | None

    def describe(self) -> str:
        platform = f"{self.platform}-{self.variant}" if self.variant else self.platform
        return f"{self.identifier} ({platform}, {'/'.join(self.architectures) or 'no architectures'})"


@dataclass(frozen=True)
class Binary:
    package: str
    name: str
    # checksum for a url, content digest for a local path
    pin: str
    # local path to watch for changes, None for a url
    watch: Path | None
    slice_dir: Path
    identifier: str
    kind: str
    artifact: Path
    headers: Path | None
    module_maps: tuple[Path, ...]
    dynamic: bool

    @property
    def framework_name(self) -> str:
        return self.artifact.stem

    @property
    def link_file(self) -> Path:
        return self.artifact / self.framework_name if self.kind == FRAMEWORK else self.artifact

    def interface_files(self) -> list[Path]:
        roots = (
            [self.artifact / d for d in INTERFACE_DIRS]
            if self.kind == FRAMEWORK
            else [self.headers] if self.headers is not None else []
        )
        return sorted(p for root in roots if root.is_dir() for p in root.rglob("*") if p.is_file())

    def to_json(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "name": self.name,
            "pin": self.pin,
            "watch": str(self.watch) if self.watch else None,
            "slice_dir": str(self.slice_dir),
            "identifier": self.identifier,
            "kind": self.kind,
            "artifact": str(self.artifact),
            "headers": str(self.headers) if self.headers else None,
            "module_maps": [str(m) for m in self.module_maps],
            "dynamic": self.dynamic,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Binary":
        return Binary(
            package=data["package"],
            name=data["name"],
            pin=data["pin"],
            watch=Path(data["watch"]) if data["watch"] else None,
            slice_dir=Path(data["slice_dir"]),
            identifier=data["identifier"],
            kind=data["kind"],
            artifact=Path(data["artifact"]),
            headers=Path(data["headers"]) if data["headers"] else None,
            module_maps=tuple(Path(m) for m in data["module_maps"]),
            dynamic=data["dynamic"],
        )


def read_slices(xcframework: Path, label: str) -> list[Slice]:
    info = xcframework / INFO_PLIST
    if not info.is_file():
        raise PackageError(f"{label} has {xcframework.name} with no {INFO_PLIST}")
    try:
        entries = read_plist(info.read_bytes())["AvailableLibraries"]
        return [
            Slice(
                identifier=entry["LibraryIdentifier"],
                platform=entry["SupportedPlatform"],
                variant=entry.get("SupportedPlatformVariant"),
                architectures=tuple(entry.get("SupportedArchitectures", ())),
                library=entry["LibraryPath"],
                headers=entry.get("HeadersPath"),
            )
            for entry in entries
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise PackageError(f"{label} has {xcframework.name} with an unreadable {INFO_PLIST} ({err!r})") from err


def select_slice(slices: Sequence[Slice], simulator: bool, label: str, arch: str = ARCH) -> Slice:
    variant = SIMULATOR if simulator else None
    for candidate in slices:
        if candidate.platform == PLATFORM and candidate.variant == variant and arch in candidate.architectures:
            return candidate
    wanted = f"{PLATFORM}-{SIMULATOR} {arch}" if simulator else f"{PLATFORM} {arch}"
    available = ", ".join(s.describe() for s in slices) or "none"
    raise PackageError(f"{label} has no {wanted} slice, its slices are {available}")


def path_pin(path: Path) -> str:
    if path.is_file():
        return sha256_file(path)
    digest = hashlib.sha256()
    info = path / INFO_PLIST
    digest.update(info.read_bytes() if info.is_file() else b"")
    for current, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            file = Path(current) / name
            digest.update(f"{file.relative_to(path).as_posix()}\0{file.stat().st_size}\n".encode())
    return digest.hexdigest()


def download(url: str, destination: Path, label: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "iosc"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, open(destination, "wb") as out:
            with Progress(f"Fetching {label}") as progress:
                length = response.headers.get("Content-Length")
                if length and length.isdigit():
                    progress.set_total(int(length))
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    progress.update(len(chunk))
    except OSError as err:
        destination.unlink(missing_ok=True)
        raise PackageError(f"{label} could not download {url} ({err})") from err


# the zip sits where swiftpm keeps its own, so one download serves both
def archive_path(cache: Path, url: str) -> Path:
    return cache / ARTIFACTS_DIR / RE_URL_SEPARATORS.sub("_", url)


def has_archive(cache: Path, url: str, checksum: str) -> bool:
    archive = archive_path(cache, url)
    return archive.is_file() and sha256_file(archive) == checksum


def fetch_archive(url: str, checksum: str, cache: Path, label: str) -> Path:
    archive = archive_path(cache, url)
    if has_archive(cache, url, checksum):
        return archive
    partial = archive.with_name(archive.name + ".partial")
    get_reporter().info(f"downloading {url}")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            download(url, partial, label)
            break
        except PackageError as err:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise PackageError(f"{err}, gave up after {attempt} attempts") from err
            get_reporter().warn(f"{err}, retrying")
            time.sleep(RETRY_DELAY * attempt)
    actual = sha256_file(partial)
    if actual != checksum:
        partial.unlink()
        raise PackageError(
            f"{label} downloaded {url} with checksum {actual}, the package declares {checksum}"
        )
    os.replace(partial, archive)
    return archive


def _safe_members(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for name in archive.namelist():
        if not (destination / name).resolve().is_relative_to(root):
            raise PackageError(f"{archive.filename} holds {name}, which lies outside its folder")


def _find_xcframework(folder: Path) -> Path | None:
    for depth in ("*", "*/*"):
        for candidate in sorted(folder.glob(depth + SUFFIX)):
            if (candidate / INFO_PLIST).is_file():
                return candidate
    return None


def unpack(archive: Path, destination: Path, label: str) -> Path:
    found = _find_xcframework(destination) if destination.is_dir() else None
    if found is not None:
        return found
    staging = destination.with_name(destination.name + ".partial")
    rmtree_force(staging)
    try:
        with zipfile.ZipFile(archive) as zipped:
            _safe_members(zipped, staging)
            zipped.extractall(staging)
    except zipfile.BadZipFile as err:
        raise PackageError(f"{label} has {archive.name}, which is not a zip ({err})") from err
    rmtree_force(destination)
    os.replace(staging, destination)
    found = _find_xcframework(destination)
    if found is None:
        raise PackageError(f"{label} has {archive.name}, which holds no {SUFFIX}")
    return found


# a universal file wraps the arm64 slice, an archive may sit inside it
def _thin(data: bytes) -> bytes:
    return data if data[: len(STATIC_MAGIC)] == STATIC_MAGIC else macho.thin_arm64(data)


def is_archive(path: Path, label: str) -> bool:
    try:
        return _thin(path.read_bytes())[: len(STATIC_MAGIC)] == STATIC_MAGIC
    except MachOError:
        return False


def is_dynamic(binary: Path, label: str) -> bool:
    try:
        thin = _thin(binary.read_bytes())
        if thin[: len(STATIC_MAGIC)] == STATIC_MAGIC:
            return False
        return macho.read_header(thin).filetype == macho.MH_DYLIB
    except MachOError as err:
        raise PackageError(f"{label} has {binary.name}, which {err}") from err


def _module_maps(headers: Path | None) -> tuple[Path, ...]:
    if headers is None or not headers.is_dir():
        return ()
    return tuple(sorted(headers.glob("*.modulemap")))


def describe_slice(
    xcframework: Path, chosen: Slice, package: str, name: str, pin: str, watch: Path | None, label: str
) -> Binary:
    slice_dir = xcframework / chosen.identifier
    artifact = slice_dir / chosen.library
    if not artifact.exists():
        raise PackageError(f"{label} has slice {chosen.identifier} without {chosen.library}")
    if artifact.suffix == ".framework":
        dynamic = is_dynamic(artifact / artifact.stem, label)
        return Binary(package, name, pin, watch, slice_dir, chosen.identifier, FRAMEWORK, artifact, None, (), dynamic)
    if artifact.suffix != ".a":
        raise PackageError(
            f"{label} has slice {chosen.identifier} holding {chosen.library}, "
            f"which is neither a framework nor a static library"
        )
    headers = slice_dir / chosen.headers if chosen.headers else None
    if not is_archive(artifact, label):
        raise PackageError(f"{label} has {chosen.library}, which is not a static archive")
    return Binary(
        package, name, pin, watch, slice_dir, chosen.identifier, LIBRARY, artifact, headers,
        _module_maps(headers), False,
    )


def prepare_url(
    package: str, name: str, url: str, checksum: str, cache: Path, simulator: bool, label: str
) -> Binary:
    scheme = url.split(":", 1)[0].lower()
    if scheme not in URL_SCHEMES:
        raise PackageError(f"{label} has a binary at {url}, iosc fetches only https urls")
    archive = fetch_archive(url, checksum.lower(), cache, label)
    xcframework = unpack(archive, cache / UNPACKED_DIR / checksum.lower(), label)
    chosen = select_slice(read_slices(xcframework, label), simulator, label)
    return describe_slice(xcframework, chosen, package, name, checksum.lower(), None, label)


def prepare_path(
    package: str, name: str, path: Path, cache: Path, simulator: bool, label: str
) -> Binary:
    if not path.exists():
        raise PackageError(f"{label} has a binary at {path}, which does not exist")
    pin = path_pin(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        xcframework = unpack(path, cache / UNPACKED_DIR / pin, label)
    elif path.suffix == SUFFIX:
        xcframework = path
    else:
        raise PackageError(f"{label} has a binary at {path}, which is neither a {SUFFIX} nor a zip")
    chosen = select_slice(read_slices(xcframework, label), simulator, label)
    return describe_slice(xcframework, chosen, package, name, pin, path, label)


def prepare(
    package: str, name: str, declared: dict[str, Any], package_root: Path, cache: Path, simulator: bool
) -> Binary:
    label = f"package {package} target {name}"
    url, checksum, path = declared.get("url"), declared.get("checksum"), declared.get("path")
    if url and checksum:
        return prepare_url(package, name, url, checksum, cache, simulator, label)
    if path and not url:
        return prepare_path(package, name, package_root / path, cache, simulator, label)
    raise PackageError(f"{label} is a prebuilt binary iosc cannot read, it needs a path or a url with a checksum")


def include_dirs(binaries: Iterable[Binary]) -> list[Path]:
    return [b.headers for b in binaries if b.kind == LIBRARY and b.headers is not None]


def framework_dirs(binaries: Iterable[Binary]) -> list[Path]:
    return [b.slice_dir for b in binaries if b.kind == FRAMEWORK]


# module.modulemap is found through -I, any other name needs pointing at
def module_map_flags(binaries: Iterable[Binary]) -> list[str]:
    flags: list[str] = []
    for binary in binaries:
        for module_map in binary.module_maps:
            if module_map.name != DEFAULT_MODULE_MAP:
                flags.extend(["-Xcc", f"-fmodule-map-file={module_map}"])
    return flags


def archives(binaries: Iterable[Binary]) -> list[Path]:
    return [b.artifact for b in binaries if b.kind == LIBRARY]


def linked_frameworks(binaries: Iterable[Binary]) -> list[str]:
    return [b.framework_name for b in binaries if b.kind == FRAMEWORK]


def embedded_frameworks(binaries: Iterable[Binary]) -> list[Path]:
    return [b.artifact for b in binaries if b.kind == FRAMEWORK and b.dynamic]


def identity(binaries: Iterable[Binary]) -> list[list[str]]:
    return [[b.name, b.pin, b.identifier] for b in binaries]
