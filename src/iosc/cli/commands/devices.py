from iosc.core import logging
from iosc.device import Device, list_devices, list_network_devices


def run(args) -> None:
    found = list_network_devices() if args.wifi else list_devices()
    rows = []
    for info in found:
        row = {"udid": info.serial, "connection": info.connection_type}
        if info.address:
            row["address"] = info.address
        if args.verbose:
            row.update(Device(info).info_dict())
        rows.append(row)

    if args.json:
        logging.result({"devices": rows})
        return

    if not rows:
        if args.wifi:
            logging.info("no devices on the network, run 'iosc wifi enable' once over usb")
        else:
            logging.info("no devices, plug in an iPhone and trust this computer")
        return
    for row in rows:
        name = row.get("DeviceName")
        version = row.get("ProductVersion")
        suffix = f"  {name} iOS {version}" if name else ""
        address = f" {row['address']}" if "address" in row else ""
        logging.info(f"{row['udid']}  {row['connection']}{address}{suffix}")
