from pathlib import Path

from iosc.device import Device, find_device, find_wifi_device


def project_dir(args) -> Path:
    return Path(args.project) if args.project else Path.cwd()


def device_from_args(args) -> Device:
    if getattr(args, "wifi", False):
        return Device(find_wifi_device(args.udid))
    return Device(find_device(args.udid))
