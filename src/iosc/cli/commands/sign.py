import getpass
from pathlib import Path

from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.codesign.identity import identity_from_files
from iosc.core import logging
from iosc.core.errors import UsageError
from iosc.device import list_devices
from iosc.formats.mobileprovision import Profile
from iosc.formats.pki import SigningIdentity


def ask_password(apple_id: str):
    def prompt() -> str:
        return getpass.getpass(f"Apple ID password for {apple_id}: ")

    return prompt


def ask_code() -> str:
    return input("Two factor code: ")


def from_files(args) -> tuple[SigningIdentity, Profile | Path | None]:
    if args.certificate is None or args.key is None:
        raise UsageError("--certificate and --key are given together")
    identity = identity_from_files(args.key, args.certificate)
    profile = Path(args.profile) if args.profile else None
    return identity, profile


# apple id signing binds the profile to one device
# take the udid from the sole connected device, like install does
def resolve_udid(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    devices = list_devices()
    if not devices:
        raise UsageError(
            "signing with an Apple ID needs a connected device, plug in an iPhone or pass --udid"
        )
    if len(devices) > 1:
        names = ", ".join(f"{d.serial}" for d in devices)
        raise UsageError(f"more than one device connected, pass --udid to pick one of {names}")
    return devices[0].serial


# two ways to sign in flag order. explicit files, apple id
def resolve_material(
    args, device_name: str
) -> tuple[SigningIdentity | None, Profile | Path | None, str | None]:
    if args.certificate is not None or args.key is not None:
        identity, profile = from_files(args)
        return identity, profile, args.udid

    if args.apple_id is None:
        raise UsageError(
            "signing needs --apple-id, or --certificate and --key. "
            "an ad hoc signature is rejected by installd with 0xe8008014"
        )

    udid = resolve_udid(args.udid)
    project = pipeline.open_project(project_dir(args))
    material = pipeline.signing_material(
        project.manifest.bundle_id,
        udid,
        args.apple_id,
        password_callback=ask_password(args.apple_id),
        code_callback=ask_code,
        device_name=device_name,
        team_id=args.team_id,
        revoke_and_reissue=args.revoke_and_reissue,
    )
    return material.identity, material.profile, udid


def run(args) -> None:
    project = pipeline.open_project(project_dir(args))
    identity, profile, udid = resolve_material(args, project.product)

    result = pipeline.sign(
        project_dir(args),
        identity=identity,
        profile=profile,
        udid=udid,
        force=args.force,
    )
    summary = result.summary

    if args.json:
        logging.result(
            {
                "app": str(result.app),
                "identifier": summary.identifier,
                "cdhash": summary.cdhash,
                "team_id": summary.team_id,
                "profile": summary.profile,
                "adhoc": summary.adhoc,
                "resources": summary.resources,
            }
        )
        return
    kind = "ad hoc" if summary.adhoc else f"team {summary.team_id}"
    logging.success(f"signed {result.app} as {summary.identifier} {kind}")
