from dataclasses import asdict
from pathlib import Path

from iosc.config import paths
from iosc.core import logging
from iosc.core.errors import UsageError
# import from submodules directly
# iosc.sdk re-exports extract and verify, which shadow the same-named submodules
from iosc.sdk.extract import extract as extract_sdk
from iosc.sdk.layout import from_root as sdk_layout_from_root
from iosc.sdk.verify import verify as verify_sdk


def run_extract(args) -> None:
    source = Path(args.xip)
    if not source.is_file():
        raise UsageError(f"no such file {source}")

    logging.step(f"extracting {source.name}")
    stats = extract_sdk(source, resume=not args.no_resume)

    if args.json:
        logging.result(asdict(stats))
        return
    logging.success(
        f"wrote {stats.files_written:,} files and {stats.bytes_written:,} bytes "
        f"to {paths.sdk_extract_root().path}"
    )


def run_status(args) -> None:
    asset = paths.sdk_root()
    present = paths.exists(asset)
    version = None
    problems: list[str] = []
    if present:
        try:
            version = sdk_layout_from_root().platform_version
        except Exception as err:
            problems.append(str(err))
            present = False

    if args.json:
        logging.result(
            {
                "root": str(asset.path),
                "present": present,
                "version": version,
                "problems": problems,
            }
        )
        return

    if not present:
        logging.info(f"no SDK at {asset.path}")
        for problem in problems:
            logging.warn(problem)
        logging.info(asset.hint)
        return
    logging.success(f"iOS SDK {version} at {asset.path}")


def run_verify(args) -> None:
    root = paths.require(paths.sdk_root())
    checked, problems = verify_sdk(root)

    if args.json:
        logging.result(
            {"root": str(root), "checked": checked, "problems": problems}
        )
        return

    for problem in problems:
        logging.warn(problem)
    if problems:
        logging.info(f"checked {checked:,} entries, {len(problems)} problems")
        return
    logging.success(f"checked {checked:,} entries, no problems")


HANDLERS = {"extract": run_extract, "status": run_status, "verify": run_verify}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
