from iosc.core import logging
from iosc.core.errors import MissingExternalAsset
from iosc.device import Device, find_device, has_wifi_pairing, pair_wifi


def wifi_pairing(udid: str) -> bool | None:
    try:
        return has_wifi_pairing(udid)
    except MissingExternalAsset:
        return None


def run(args) -> None:
    device = Device(find_device(args.udid))
    if args.subcommand == "pair":
        path = pair_wifi(device.udid)
        if args.json:
            logging.result({"udid": device.udid, "pair_record": str(path)})
            return
        logging.success(f"paired {device.udid} for wifi installs, record at {path}")
        return

    if args.subcommand != "status":
        device.set_wifi(args.subcommand == "enable")
    enabled = device.wifi_enabled()
    paired = wifi_pairing(device.udid)

    if args.json:
        logging.result({"udid": device.udid, "wifi": enabled, "wifi_install_paired": paired})
        return
    state = "enabled" if enabled else "disabled"
    logging.success(f"wifi connections {state} on {device.udid}")
    if paired is None:
        logging.detail("wifi installs need pymobiledevice3, not installed")
    else:
        logging.detail("wifi installs " + ("paired" if paired else "not paired, run 'iosc wifi pair'"))
