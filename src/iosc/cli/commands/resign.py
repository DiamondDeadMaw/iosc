from iosc.build import resign_app
from iosc.cli.args import device_from_args
from iosc.cli.commands.install import report_progress
from iosc.cli.commands.sign import ask_code, ask_password, checked_anisette, resolve_udid
from iosc.core import logging


def run(args) -> None:
    device = device_from_args(args) if args.install or args.wifi else None
    udid = device.udid if device is not None else resolve_udid(args.udid)

    result = resign_app(
        args.source,
        udid,
        args.apple_id,
        output=args.output,
        bundle_id=args.bundle_id,
        password_callback=ask_password(args.apple_id),
        code_callback=ask_code,
        team_id=args.team_id,
        refresh_profile=args.refresh_profile,
        anisette_provider=checked_anisette(getattr(args, "anisette", None)),
    )
    for warning in result.warnings:
        logging.warn(warning)
    logging.step(f"signed {result.bundle_id} into {result.output}")

    if device is not None and args.install:
        device.install_app(result.output, progress=report_progress)

    expires = result.expires.isoformat() if result.expires else None
    if args.json:
        logging.result(
            {
                "output": str(result.output),
                "bundle_id": result.bundle_id,
                "udid": udid,
                "team_id": result.team_id,
                "profile": result.profile,
                "expires": expires,
                "cdhash": result.cdhash,
                "installed": args.install,
            }
        )
        return
    where = f", installed on {udid}" if args.install else ""
    logging.success(f"resigned {result.bundle_id}, profile expires {expires}{where}")
