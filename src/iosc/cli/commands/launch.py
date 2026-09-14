from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.core import logging
from iosc.device import find_device, launch_app


def run(args) -> None:
    project = pipeline.open_project(project_dir(args))
    bundle_id = project.manifest.bundle_id
    device = find_device(args.udid)

    env = dict(pair.split("=", 1) for pair in args.env)
    app_args = args.app_args[1:] if args.app_args[:1] == ["--"] else args.app_args
    logging.step(f"launching {bundle_id} on {device.serial}")
    result = launch_app(
        device.serial,
        bundle_id,
        args=app_args,
        env=env,
        kill_existing=not args.no_kill_existing,
        suspended=args.suspended,
    )

    if args.json:
        logging.result({"udid": device.serial, "bundle_id": bundle_id, "pid": result.pid})
        return
    if result.pid is not None:
        logging.success(f"launched {bundle_id} on {device.serial}, pid {result.pid}")
    else:
        logging.success(f"launched {bundle_id} on {device.serial}")
        logging.detail(result.raw_output)
