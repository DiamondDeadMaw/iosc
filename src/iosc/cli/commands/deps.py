from iosc.build import pipeline
from iosc.build.pipeline import DependencyReport
from iosc.cli.args import project_dir
from iosc.config.manifest import REQUIREMENT_KINDS, Requirement
from iosc.core import logging
from iosc.core.color import BOLD, CYAN, DIM, paint
from iosc.core.errors import UsageError


def _report_json(report: DependencyReport) -> dict[str, object]:
    return {
        "dependencies": [
            {
                "name": d.name,
                "identity": d.identity,
                "source": d.source,
                "rule": d.rule,
                "pinned": d.pinned,
            }
            for d in report.dependencies
        ],
        "indirect": report.indirect,
        "targets": [
            {
                "module": t.module,
                "package": t.package,
                "swift_version": t.swift_version,
                "bundle": t.bundle,
            }
            for t in report.targets
        ],
    }


def _print_report(report: DependencyReport) -> None:
    if not report.dependencies:
        logging.info("no dependencies in iosc.toml")
        return
    width = max(len(d.name) for d in report.dependencies) + 2
    for dependency in report.dependencies:
        pinned = f"  pinned {dependency.pinned}" if dependency.pinned else ""
        logging.info(
            f"{paint(f'{dependency.name:<{width}}', BOLD, CYAN)}{dependency.rule}{pinned}"
        )
        logging.info(paint(f"{' ' * width}{dependency.source}", DIM))
    if report.indirect:
        logging.info("")
        logging.info(paint("pulled in by other packages", BOLD, CYAN))
        for identity, version in report.indirect.items():
            logging.info(f"  {identity}  {version}")
    logging.info("")
    logging.info(paint(f"{len(report.targets)} modules", BOLD, CYAN))
    for target in report.targets:
        bundle = f", resources in {target.bundle}" if target.bundle else ""
        logging.info(f"  {target.module}  from {target.package}, Swift {target.swift_version}{bundle}")


def _finish(args, report: DependencyReport, message: str) -> None:
    if args.json:
        logging.result(_report_json(report))
        return
    _print_report(report)
    logging.success(message)


def run_show(args) -> None:
    report = pipeline.show_dependencies(project_dir(args))
    if args.json:
        logging.result(_report_json(report))
        return
    _print_report(report)


def run_resolve(args) -> None:
    report = pipeline.resolve_dependencies(project_dir(args))
    _finish(args, report, "resolved, versions pinned in Package.resolved")


def run_update(args) -> None:
    report = pipeline.update_dependencies(project_dir(args), args.packages)
    _finish(args, report, "updated, versions pinned in Package.resolved")


def run_add(args) -> None:
    given = [kind for kind in REQUIREMENT_KINDS if getattr(args, kind) is not None]
    if len(given) > 1:
        raise UsageError(f"pass at most one of --{', --'.join(REQUIREMENT_KINDS)}")
    requirement = Requirement(given[0], getattr(args, given[0])) if given else None
    dependency, report = pipeline.add_dependency(
        project_dir(args), args.source, args.name, requirement, args.product
    )
    rule = f" {dependency.requirement.kind} {dependency.requirement.value}" if dependency.requirement else ""
    _finish(args, report, f"added {dependency.name}{rule} to iosc.toml")


HANDLERS = {"show": run_show, "resolve": run_resolve, "update": run_update, "add": run_add}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
