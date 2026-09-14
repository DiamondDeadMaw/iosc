from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.core import logging
from iosc.core.errors import InstallError
from iosc.device import Device, find_device


def bundle_to_install(project) -> str:
    if project.layout.ipa.is_file():
        return str(project.layout.ipa)
    if project.layout.app.is_dir():
        return str(project.layout.app)
    raise InstallError(
        f"nothing to install at {project.layout.app}, run 'iosc build' and 'iosc sign' first"
    )


def report_progress(percent: int, status: str) -> None:
    logging.detail(f"{percent:3d}% {status}")


def run(args) -> None:
    project = pipeline.open_project(project_dir(args))
    target = bundle_to_install(project)
    device = Device(find_device(args.udid))

    logging.step(f"installing {target} on {device.udid}")
    result = device.install_app(target, progress=report_progress)

    if args.json:
        logging.result(
            {
                "udid": device.udid,
                "installed": target,
                "bundle_id": project.manifest.bundle_id,
                "status": result.get("Status", "Complete"),
            }
        )
        return
    logging.success(f"installed {project.manifest.bundle_id} on {device.udid}")
