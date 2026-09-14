from pathlib import Path

from iosc.config import paths
from iosc.core import logging
from iosc.core.errors import UsageError
from iosc.debug import symbolicate
from iosc.device import find_device, list_reports, pull_reports


def run_list(args) -> None:
    device = find_device(args.udid)
    reports = list_reports(
        device.serial, remote_path=args.remote_path, depth=args.depth
    )

    if args.json:
        logging.result({"reports": reports})
        return
    if not reports:
        logging.info("no crash reports found")
        return
    for report in reports:
        logging.info(report)


def run_pull(args) -> None:
    device = find_device(args.udid)
    logging.step(f"pulling crash reports from {device.serial} into {args.out}")
    pulled = pull_reports(
        device.serial,
        args.out,
        remote_path=args.remote_path,
        match=args.match,
        erase=args.erase,
    )

    if args.json:
        logging.result({"pulled": pulled})
        return
    if not pulled:
        logging.info("no matching crash reports found")
        return
    for path in pulled:
        logging.success(path)


# a .dSYM is a bundle, the actual DWARF binary llvm-symbolizer wants sits
# under Contents/Resources/DWARF, named after the original executable
def _dsym_dwarf_binary(dsym_path: Path) -> Path:
    dwarf_dir = dsym_path / "Contents" / "Resources" / "DWARF"
    if not dwarf_dir.is_dir():
        raise UsageError(f"{dsym_path} does not look like a .dSYM bundle")
    candidates = [p for p in dwarf_dir.iterdir() if p.is_file()]
    if not candidates:
        raise UsageError(f"no DWARF binary found inside {dsym_path}")
    return candidates[0]


def run_symbolicate(args) -> None:
    report_path = Path(args.report)
    if not report_path.is_file():
        raise UsageError(f"no such crash report {report_path}")

    binaries = []
    binary_paths = []
    for binary_path in args.binary:
        path = Path(binary_path)
        if not path.is_file():
            raise UsageError(f"no such binary {path}")
        binaries.append(path.read_bytes())
        binary_paths.append(path)

    for dsym in args.dsym:
        dsym_path = Path(dsym)
        if not dsym_path.is_dir():
            raise UsageError(f"no such dSYM bundle {dsym_path}")
        dwarf_binary = _dsym_dwarf_binary(dsym_path)
        binaries.append(dwarf_binary.read_bytes())
        binary_paths.append(dwarf_binary)

    llvm_symbolizer = None
    if args.dsym:
        symbolizer_asset = paths.llvm_symbolizer_tool()
        if paths.exists(symbolizer_asset):
            llvm_symbolizer = symbolizer_asset.path
        else:
            logging.warn(f"{symbolizer_asset.hint} file:line will not be available")

    text = report_path.read_text(encoding="utf-8", errors="replace")
    backtrace = symbolicate.symbolicate_crash(
        text,
        binaries,
        thread_index=args.thread,
        binary_paths=binary_paths,
        llvm_symbolizer=llvm_symbolizer,
    )

    if args.json:
        logging.result({"backtrace": backtrace})
        return
    logging.info(backtrace)


HANDLERS = {"list": run_list, "pull": run_pull, "symbolicate": run_symbolicate}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
