import datetime
import os
import plistlib
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil

from iosc.build import package
from iosc.build.pipeline import signing_material
from iosc.codesign.signer import sign_app_recursive
from iosc.core import BuildError, rmtree_force

PAYLOAD_DIR = "Payload"


@dataclass(frozen=True)
class ResignResult:
    output: Path
    bundle_id: str
    team_id: str | None
    profile: str | None
    expires: datetime.datetime | None
    cdhash: str
    warnings: tuple[str, ...]


def default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}-signed{source.suffix}")


def stage_app(source: Path, work: Path) -> Path:
    if source.is_dir():
        if source.suffix != ".app":
            raise BuildError(f"{source} is not a .app bundle")
        target = work / source.name
        shutil.copytree(source, target, symlinks=True)
        return target
    if source.is_file() and source.suffix == ".ipa":
        with zipfile.ZipFile(source) as archive:
            archive.extractall(work)
        apps = sorted((work / PAYLOAD_DIR).glob("*.app"))
        if len(apps) != 1:
            raise BuildError(f"{source} must hold exactly one app under {PAYLOAD_DIR}/")
        return apps[0]
    raise BuildError(f"{source} is neither a .app bundle nor an .ipa")


def read_info(bundle: Path) -> tuple[dict, plistlib.PlistFormat]:
    data = (bundle / "Info.plist").read_bytes()
    fmt = plistlib.FMT_BINARY if data.startswith(b"bplist") else plistlib.FMT_XML
    return plistlib.loads(data), fmt


def write_info(bundle: Path, info: dict, fmt: plistlib.PlistFormat) -> None:
    (bundle / "Info.plist").write_bytes(plistlib.dumps(info, fmt=fmt))


# extension ids prefix the app id. rebase them
def rename_bundle(app: Path, new_id: str) -> None:
    info, fmt = read_info(app)
    old_id = info.get("CFBundleIdentifier")
    if not old_id:
        raise BuildError(f"{app} has no CFBundleIdentifier")
    info["CFBundleIdentifier"] = new_id
    write_info(app, info, fmt)

    plugins = app / "PlugIns"
    if not plugins.is_dir():
        return
    for extension in sorted(plugins.glob("*.appex")):
        ext_info, ext_fmt = read_info(extension)
        ext_id = ext_info.get("CFBundleIdentifier", "")
        if ext_id.startswith(old_id + "."):
            ext_info["CFBundleIdentifier"] = new_id + ext_id[len(old_id):]
            write_info(extension, ext_info, ext_fmt)


def publish(app: Path, output: Path) -> None:
    if output.suffix == ".ipa":
        package.write_ipa(app, output)
        return
    if output.exists():
        rmtree_force(output)
    os.replace(app, output)


def resign_app(
    source: Path | str,
    udid: str,
    apple_id: str,
    output: Path | str | None = None,
    bundle_id: str | None = None,
    password_callback: Callable[[], str] | None = None,
    code_callback: Callable[[], str] | None = None,
    device_name: str | None = None,
    team_id: str | None = None,
) -> ResignResult:
    source = Path(source).resolve()
    target = Path(output).resolve() if output is not None else default_output(source)
    if target == source:
        raise BuildError("resign writes a new bundle, pick an output other than the source")
    target.parent.mkdir(parents=True, exist_ok=True)

    # same volume as output. publish is a rename
    work = Path(tempfile.mkdtemp(prefix=".resign-", dir=target.parent))
    try:
        app = stage_app(source, work)
        if bundle_id is not None:
            rename_bundle(app, bundle_id)
        info, _ = read_info(app)
        identifier = info.get("CFBundleIdentifier")
        if not identifier:
            raise BuildError(f"{source} has no CFBundleIdentifier")

        material = signing_material(
            identifier,
            udid,
            apple_id,
            password_callback=password_callback,
            code_callback=code_callback,
            device_name=device_name,
            team_id=team_id,
        )
        signed = sign_app_recursive(
            app, identity=material.identity, profile=material.profile, udid=udid
        )
        publish(app, target)
    finally:
        rmtree_force(work)

    return ResignResult(
        output=target,
        bundle_id=identifier,
        team_id=signed.team_id,
        profile=signed.profile,
        expires=material.profile.expiration_date,
        cdhash=signed.cdhash.hex(),
        warnings=signed.warnings,
    )
