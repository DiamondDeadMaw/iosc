from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.core import logging


def run(args) -> None:
    removed = pipeline.clean(project_dir(args))
    if args.json:
        logging.result({"removed": str(removed)})
        return
    logging.success(f"removed {removed}")
