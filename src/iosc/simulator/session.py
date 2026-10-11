import ctypes
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any

from iosc.config import paths
from iosc.core import SimulatorError, get_reporter, write_atomic
from iosc.core.process import spawn_background
from iosc.core.progress import Heartbeat
from iosc.simulator import control
from iosc.simulator.control import Endpoint
from iosc.simulator.install import Installation, check_protocol

LOG_LEVELS = ("off", "default", "info", "debug")
LOGS_DIR_NAME = "simulator_logs"
SESSION_NAME = "sim_session.json"
READY_POLL = 1.0
SHUTDOWN_TIMEOUT = 60.0

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


# a boot iosc started
@dataclass(frozen=True)
class Session:
    pid: int
    project: str | None
    log: str
    events: str


@dataclass(frozen=True)
class Boot:
    endpoint: Endpoint
    hello: dict[str, Any]
    # None for a boot iosc did not start
    session: Session | None

    @property
    def pid(self) -> int:
        return int(self.hello["pid"])


def session_path() -> Path:
    return paths.state_dir() / SESSION_NAME


def read_session() -> Session | None:
    try:
        return Session(**json.loads(session_path().read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def write_session(session: Session) -> None:
    write_atomic(session_path(), (json.dumps(asdict(session), indent=2) + "\n").encode("utf-8"))


def clear_session() -> None:
    session_path().unlink(missing_ok=True)


def logs_dir(project: Path | None) -> Path:
    return project / LOGS_DIR_NAME if project else paths.state_dir() / LOGS_DIR_NAME


# without a project there is no app to log, so nothing streams unless asked
def log_level(requested: str | None, has_project: bool, log_all: bool) -> str:
    if requested is not None:
        return requested
    return "default" if has_project or log_all else "off"


# os_log lines whose text names the app, or every line
def log_pattern(bundle_id: str | None, log_all: bool) -> str:
    if log_all or bundle_id is None:
        return "."
    return bundle_id.replace(".", r"\.")


def boot_argv(logs: Path, log_level: str, pattern: str, passthrough: list[str]) -> list[str]:
    argv = ["--boot", "--window", "-e", "JSC_useJIT=0", "--log", str(logs), "--events", str(logs)]
    if log_level != "off":
        argv += ["--events-log", pattern]
    if log_level == "info":
        argv.append("--log-info")
    elif log_level == "debug":
        argv.append("--log-debug")
    return argv + passthrough


def wait_for_exit(pid: int, timeout: float) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def current() -> Boot | None:
    endpoint = control.read_endpoint()
    hello = None
    if endpoint is not None:
        try:
            hello = control.hello(endpoint)
        except SimulatorError:
            pass
    session = read_session()
    if hello is None:
        if session is not None:
            clear_session()
        return None
    check_protocol(int(hello["protocol"]))
    owned = session if session is not None and session.pid == int(hello["pid"]) else None
    return Boot(endpoint, hello, owned)


def require_current() -> Boot:
    boot = current()
    if boot is None:
        raise SimulatorError("the simulator is not booted. Run 'iosc sim boot' or 'iosc sim run'")
    return boot


def stop(boot: Boot) -> None:
    control.shutdown(boot.endpoint)
    with Heartbeat("stopping the simulator"):
        exited = wait_for_exit(boot.pid, SHUTDOWN_TIMEOUT)
    if not exited:
        raise SimulatorError(
            f"the simulator (pid {boot.pid}) did not exit within {SHUTDOWN_TIMEOUT:.0f} s of the shutdown request"
        )
    clear_session()


def restart_reason(
    session: Session, hello: dict[str, Any], project: Path | None, argv: list[str]
) -> str | None:
    wanted = str(project) if project else None
    if session.project != wanted:
        return f"it was booted for {session.project or 'no project'}"
    if hello.get("argv") != argv:
        return "its log settings differ"
    return None


# reuses a matching boot iosc started, restarts one that does not match
def ensure_booted(
    installation: Installation,
    project: Path | None,
    argv: list[str],
    restart: bool,
    timeout: float,
) -> Boot:
    boot = current()
    if boot is not None:
        if boot.session is None:
            raise SimulatorError(
                f"a simulator iosc did not start is running (pid {boot.pid}). "
                "Stop it with 'iosc sim stop --force' first"
            )
        reason = (
            "--restart was given"
            if restart
            else restart_reason(boot.session, boot.hello, project, argv)
        )
        if reason is None:
            get_reporter().step(f"reusing the simulator, pid {boot.pid}")
            return boot
        get_reporter().step(f"restarting the simulator, {reason}")
        stop(boot)
    return start(installation, project, argv, timeout)


def start(
    installation: Installation, project: Path | None, argv: list[str], timeout: float
) -> Boot:
    logs = logs_dir(project)
    child = spawn_background(
        [str(installation.iossim), *argv], cwd=installation.root, detached=True
    )
    deadline = time.monotonic() + timeout
    session = None
    with Heartbeat("booting the simulator"):
        while True:
            code = child.poll()
            if code is not None:
                clear_session()
                raise SimulatorError(
                    f"the simulator exited with code {code} while booting, its log is in {logs}"
                )
            endpoint = control.read_endpoint()
            if endpoint is not None and endpoint.pid == child.pid:
                try:
                    hello = control.hello(endpoint)
                    if session is None:
                        session = Session(
                            child.pid,
                            str(project) if project else None,
                            hello["log"],
                            hello["events"],
                        )
                        write_session(session)
                    if control.status(endpoint)["bridge_ready"]:
                        return Boot(endpoint, hello, session)
                except SimulatorError:
                    pass
            if time.monotonic() > deadline:
                raise SimulatorError(
                    f"the simulator did not become ready within {timeout:.0f} s. "
                    f"It is still running as pid {child.pid}, stop it with 'iosc sim stop'"
                )
            time.sleep(READY_POLL)
