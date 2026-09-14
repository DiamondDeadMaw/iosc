from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.cli.commands import install as install_cmd
from iosc.cli.commands import sign as sign_cmd
from iosc.core import logging
from iosc.core.errors import DeviceError, MissingExternalAsset
from iosc.device import Device, find_device, launch_app


def run(args) -> None:
    directory = project_dir(args)
    device = Device(find_device(args.udid))

    built = pipeline.build(directory, force=args.force)
    logging.step(f"built {built.app}")

    identity, profile = sign_cmd.resolve_material(args, built.product, device.udid)
    signed = pipeline.sign(
        directory,
        identity=identity,
        profile=profile,
        udid=device.udid,
        force=args.force,
    )
    logging.step(f"signed {signed.summary.identifier}")

    device.install_app(str(signed.app), progress=install_cmd.report_progress)
    project = pipeline.open_project(directory)
    logging.success(f"installed {project.manifest.bundle_id} on {device.udid}")

    if args.no_log:
        return

    if args.launch:
        launch_or_ask_by_hand(device.udid, project.manifest.bundle_id)
    else:
        logging.info("tap the app on the device, its output follows")

    for line in device.syslog(contains=built.product):
        logging.info(line)


# tunnel or pymobiledevice3 can be missing
# degrade to the fallback instead of failing the build
def launch_or_ask_by_hand(udid: str, bundle_id: str) -> None:
    try:
        result = launch_app(udid, bundle_id)
    except (MissingExternalAsset, DeviceError) as error:
        logging.warn(f"could not launch automatically: {error}")
        logging.info("tap the app on the device, its output follows")
        return

    if result.pid is not None:
        logging.success(f"launched {bundle_id}, pid {result.pid}")
    else:
        logging.success(f"launched {bundle_id}")
