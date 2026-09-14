from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.core import logging


def run(args) -> None:
    ipa = pipeline.package_ipa(project_dir(args))
    if args.json:
        logging.result({"ipa": str(ipa), "size": ipa.stat().st_size})
        return
    logging.success(f"wrote {ipa}")
