from iosc.build import pipeline
from iosc.cli.args import project_dir
from iosc.core import logging


def run(args) -> None:
    result = pipeline.build(project_dir(args), force=args.force)
    if args.json:
        logging.result(
            {
                "app": str(result.app),
                "product": result.product,
                "ran": list(result.report.ran),
                "skipped": list(result.report.skipped),
            }
        )
        return
    logging.success(f"built {result.app}")
