import os
from pathlib import Path
import stat
import zipfile

from iosc.build.graph import BuildLayout, Stage, fingerprint
from iosc.core import BundleError

STAGE_NAME = "package"
PAYLOAD_DIR = "Payload"

MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
MODE_EXECUTABLE = 0o100755
MODE_REGULAR = 0o100644
CREATE_SYSTEM_UNIX = 3


# sort key is posix name, not path. host case folding doesnt apply
def bundle_files(app_dir: Path) -> list[Path]:
    found = [p for p in app_dir.rglob("*") if p.is_file() or p.is_symlink()]
    return sorted(found, key=lambda p: p.relative_to(app_dir).as_posix())


def _is_executable(path: Path, content: bytes) -> bool:
    if content[:4] in MACHO_MAGICS:
        return True
    if path.suffix == ".dylib":
        return True
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def _symlink_entry(archive: zipfile.ZipFile, arcname: str, target: Path) -> None:
    info = zipfile.ZipInfo(arcname)
    info.create_system = CREATE_SYSTEM_UNIX
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(info, os.readlink(target).replace("\\", "/").encode("utf-8"))


# sort entries and attach fixed zip epoch, so stage cache can compare
def write_ipa(app_dir: Path, output: Path) -> Path:
    app = Path(app_dir)
    if not app.is_dir():
        raise BundleError(f"app directory not found at {app}")
    if app.suffix != ".app":
        raise BundleError(f"{app} is not a .app directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")

    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in bundle_files(app):
            relative = path.relative_to(app).as_posix()
            arcname = f"{PAYLOAD_DIR}/{app.name}/{relative}"
            if path.is_symlink():
                _symlink_entry(archive, arcname, path)
                continue
            content = path.read_bytes()
            info = zipfile.ZipInfo(arcname)
            info.create_system = CREATE_SYSTEM_UNIX
            mode = MODE_EXECUTABLE if _is_executable(path, content) else MODE_REGULAR
            info.external_attr = mode << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)

    os.replace(partial, output)
    return output


def package_stage(
    layout: BuildLayout,
    product: str,
    depends_on: tuple[str, ...] = (),
) -> Stage:
    inputs = bundle_files(layout.app)
    print_ = fingerprint({"payload": f"{PAYLOAD_DIR}/{product}.app"})

    def run() -> None:
        write_ipa(layout.app, layout.ipa)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        depends_on=depends_on,
        inputs=tuple(inputs),
        outputs=(layout.ipa,),
    )
