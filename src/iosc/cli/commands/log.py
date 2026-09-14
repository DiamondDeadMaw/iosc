from dataclasses import asdict

from iosc.core import logging
from iosc.device import Device, find_device, format_oslog_entry, stream_oslog


def run(args) -> None:
    if args.os_log:
        return _run_os_log(args)

    device = Device(find_device(args.udid))
    logging.step(f"streaming syslog from {device.udid}, ctrl c to stop")
    for line in device.syslog(contains=args.contains):
        if args.json:
            logging.result({"line": line})
        else:
            logging.info(line)


def _run_os_log(args) -> None:
    device = find_device(args.udid)
    logging.step(f"streaming unified log from {device.serial}, ctrl c to stop")
    for entry in stream_oslog(
        device.serial,
        pid=args.pid,
        process_name=args.process_name,
        match=args.match,
    ):
        if args.json:
            logging.result(asdict(entry))
        else:
            logging.info(format_oslog_entry(entry))
