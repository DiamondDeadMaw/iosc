from pathlib import Path

from iosc.build import pipeline
from iosc.build.pipeline import Project
from iosc.cli.args import project_dir
from iosc.config import manifest as manifest_module
from iosc.config import paths
from iosc.core import IoscError, SimulatorError, UsageError, logging, write_atomic
from iosc.simulator import control, install, logs, privacy, session
from iosc.simulator.screenshot import bmp_to_png


def strip_separator(values: list[str]) -> list[str]:
    return values[1:] if values[:1] == ["--"] else values


def parse_env(pairs: list[str]) -> dict[str, str]:
    env = {}
    for pair in pairs:
        key, eq, value = pair.partition("=")
        if not key or not eq:
            raise UsageError(f"--env takes KEY=VALUE, not {pair}")
        env[key] = value
    return env


# commands that work outside a project use one when the directory has an iosc.toml
def optional_project(args) -> Project | None:
    directory = project_dir(args)
    if args.project is None and not manifest_module.path_for(directory).is_file():
        return None
    return pipeline.open_project(directory, simulator=True)


def bundle_id_for(args, given: str | None) -> str:
    if given:
        return given
    project = optional_project(args)
    if project is None:
        raise UsageError("pass a bundle id, or run this inside an iosc project")
    return project.manifest.bundle_id


def warn_runtime_mismatch() -> None:
    try:
        installation = install.load()
    except IoscError:
        return
    mismatch = install.runtime_mismatch(paths.simulator_sdk_root().path, installation)
    if mismatch:
        logging.warn(mismatch)


def boot(args, project: Project | None) -> session.Boot:
    installation = install.load()
    root = project.layout.project if project else None
    pattern = session.log_pattern(project.manifest.bundle_id if project else None, args.log_all)
    passthrough = strip_separator(getattr(args, "iossim_args", []))
    level = session.log_level(args.log_level, project is not None, args.log_all)
    argv = session.boot_argv(session.logs_dir(root), level, pattern, passthrough)
    return session.ensure_booted(installation, root, argv, args.restart, args.timeout)


def follow(running: session.Boot, offset: int, app_pid: int | None, everything: bool) -> None:
    logging.info("following the simulator log, Ctrl+C to stop")
    line_filter = logs.LineFilter(app_pid, everything)
    try:
        for line in logs.follow(Path(running.hello["log"]), offset, running.pid):
            if line_filter.wanted(line):
                logging.info(line)
    except KeyboardInterrupt:
        return
    logging.info("the simulator exited")


def run_build(args) -> None:
    result = pipeline.build(project_dir(args), force=args.force, simulator=True)
    warn_runtime_mismatch()
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


def run_boot(args) -> None:
    running = boot(args, optional_project(args))
    if args.json:
        logging.result({"pid": running.pid, "log": running.hello["log"]})
        return
    logging.success(f"simulator ready, pid {running.pid}")
    logging.info(f"log {running.hello['log']}")


def run_run(args) -> None:
    directory = project_dir(args)
    project = pipeline.open_project(directory, simulator=True)
    install.load()
    if not args.no_build:
        built = pipeline.build(directory, force=args.force, simulator=True)
        warn_runtime_mismatch()
        logging.step(f"built {built.app}")
    elif not project.layout.app.is_dir():
        raise SimulatorError(f"nothing to install at {project.layout.app}, run 'iosc sim build' first")

    running = boot(args, project)
    installed = control.install(running.endpoint, project.layout.app)
    logging.step(f"installed {installed['bundle_id']}")
    if args.no_launch:
        if args.json:
            logging.result({"pid": running.pid, "bundle_id": installed["bundle_id"]})
        return

    log = Path(running.hello["log"])
    offset = logs.current_size(log)
    launched = control.launch(
        running.endpoint,
        project.manifest.bundle_id,
        parse_env(args.env),
        strip_separator(args.app_args),
    )
    if args.json:
        logging.result(
            {"pid": running.pid, "bundle_id": project.manifest.bundle_id, "app_pid": launched["pid"]}
        )
    else:
        logging.success(f"launched {project.manifest.bundle_id}, pid {launched['pid']}")
        logging.info(f"log {log}")
    if args.follow:
        follow(running, offset, launched["pid"], args.verbose)


def run_install(args) -> None:
    running = session.require_current()
    if args.app:
        app = Path(args.app).resolve()
    else:
        app = pipeline.open_project(project_dir(args), simulator=True).layout.app
    if app.suffix != ".app" or not app.is_dir():
        raise SimulatorError(f"{app} is not a built .app, run 'iosc sim build' first")
    installed = control.install(running.endpoint, app)
    if args.json:
        logging.result({"bundle_id": installed["bundle_id"], "terminated": installed["terminated"]})
        return
    logging.success(f"installed {installed['bundle_id']}")


def run_launch(args) -> None:
    running = session.require_current()
    bundle_id = bundle_id_for(args, args.bundle_id)
    launched = control.launch(
        running.endpoint, bundle_id, parse_env(args.env), strip_separator(args.app_args)
    )
    if args.json:
        logging.result({"bundle_id": bundle_id, "pid": launched["pid"]})
        return
    logging.success(f"launched {bundle_id}, pid {launched['pid']}")


def run_terminate(args) -> None:
    running = session.require_current()
    bundle_id = bundle_id_for(args, args.bundle_id)
    terminated = control.terminate(running.endpoint, bundle_id)["terminated"]
    if args.json:
        logging.result({"bundle_id": bundle_id, "terminated": terminated})
        return
    if terminated:
        logging.success(f"terminated {bundle_id}, pids {', '.join(map(str, terminated))}")
    else:
        logging.info(f"{bundle_id} was not running")


def run_status(args) -> None:
    report: dict = {"installed": False, "booted": False}
    try:
        installation = install.load()
        report.update(
            installed=True,
            root=str(installation.root),
            runtime=f"{installation.runtime} ({installation.runtime_build})",
            protocol=install.PROTOCOL,
        )
    except IoscError as err:
        report["problem"] = err.message

    running = session.current() if report["installed"] else None
    if running is not None:
        state = control.status(running.endpoint)
        report.update(
            booted=True,
            pid=running.pid,
            uptime=state["uptime"],
            ready=state["bridge_ready"],
            started_by_iosc=running.session is not None,
            project=running.session.project if running.session else None,
            log=running.hello["log"],
            apps=state["apps"],
        )

    if args.json:
        logging.result(report)
        return
    if not report["installed"]:
        logging.info(report["problem"])
        return
    logging.info(f"simulator iOS {report['runtime']} at {report['root']}, protocol {report['protocol']}")
    if not report["booted"]:
        logging.info("not booted")
        return
    if report["started_by_iosc"]:
        owner = f"booted for {report['project'] or 'no project'}"
    else:
        owner = "not started by iosc"
    logging.info(
        f"booted, pid {report['pid']}, up {report['uptime']} s, "
        f"{'ready' if report['ready'] else 'not ready yet'}, {owner}"
    )
    logging.info(f"log {report['log']}")
    for app in report["apps"]:
        logging.info(f"  {app['pid']:6} {app['bundle_id'] or '?':40} {app['name']}")


def run_stop(args) -> None:
    running = session.current()
    if running is None:
        if args.json:
            logging.result({"stopped": None})
            return
        logging.info("the simulator is not booted")
        return
    if running.session is None and not args.force:
        raise SimulatorError(
            f"the simulator (pid {running.pid}) was not started by iosc, pass --force to stop it"
        )
    session.stop(running)
    if args.json:
        logging.result({"stopped": running.pid})
        return
    logging.success(f"stopped the simulator, pid {running.pid}")


def run_screenshot(args) -> None:
    running = session.require_current()
    out = Path(args.out).resolve()
    bmp = out.with_name(out.name + ".bmp")
    try:
        shot = control.screenshot(running.endpoint, bmp)
        write_atomic(out, bmp_to_png(bmp.read_bytes()))
    finally:
        bmp.unlink(missing_ok=True)
    if args.json:
        logging.result({"path": str(out), "width": shot["width"], "height": shot["height"]})
        return
    logging.success(f"saved {shot['width']}x{shot['height']} screenshot to {out}")


def run_privacy(args) -> None:
    installation = install.load()
    bundle_id = bundle_id_for(args, args.bundle_id)
    name = privacy.set_permission(installation.tcc_db, args.action, args.service, bundle_id)
    if args.json:
        logging.result({"action": args.action, "service": name, "bundle_id": bundle_id})
        return
    logging.success(f"{args.action} {name} for {bundle_id}")


HANDLERS = {
    "build": run_build,
    "boot": run_boot,
    "run": run_run,
    "install": run_install,
    "launch": run_launch,
    "terminate": run_terminate,
    "status": run_status,
    "stop": run_stop,
    "screenshot": run_screenshot,
    "privacy": run_privacy,
}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
