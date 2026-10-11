from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time
from typing import Any
import uuid

from iosc.config import paths
from iosc.core import SimulatorError

ERROR_PIPE_BUSY = 231
PIPE_BUSY_WAIT = 30.0


@dataclass(frozen=True)
class Endpoint:
    pipe: str
    pid: int
    install_dir: Path


# the last boot to publish its pipe. it may have exited since
def read_endpoint() -> Endpoint | None:
    try:
        data = json.loads((paths.iossim_dir() / "endpoint.json").read_text(encoding="utf-8"))
        return Endpoint(data["pipe"], int(data["pid"]), Path(data["install_dir"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _open(endpoint: Endpoint):
    deadline = time.monotonic() + PIPE_BUSY_WAIT
    while True:
        try:
            return open(endpoint.pipe, "r+b", buffering=0)
        except FileNotFoundError as err:
            raise SimulatorError(f"the simulator (pid {endpoint.pid}) is not running") from err
        except OSError as err:
            if getattr(err, "winerror", None) != ERROR_PIPE_BUSY or time.monotonic() > deadline:
                raise SimulatorError(f"cannot reach the simulator control pipe, {err}") from err
            time.sleep(0.2)


def request(endpoint: Endpoint, **fields: Any) -> dict[str, Any]:
    with _open(endpoint) as pipe:
        pipe.write((json.dumps(fields, ensure_ascii=False) + "\n").encode("utf-8"))
        line = b""
        while not line.endswith(b"\n"):
            chunk = pipe.read(1 << 16)
            if not chunk:
                raise SimulatorError("the simulator closed its control pipe")
            line += chunk
    reply = json.loads(line)
    if not reply.get("ok"):
        message = reply.get("error", f"{fields.get('cmd')} failed")
        detail = reply.get("detail")
        raise SimulatorError(f"{message}\n{detail}" if detail else message)
    return reply


def hello(endpoint: Endpoint) -> dict[str, Any]:
    return request(endpoint, cmd="hello")


def status(endpoint: Endpoint) -> dict[str, Any]:
    return request(endpoint, cmd="status")


# the simulator only installs from below its own install directory
def install(endpoint: Endpoint, app: Path) -> dict[str, Any]:
    stage = endpoint.install_dir / uuid.uuid4().hex
    try:
        shutil.copytree(app, stage / app.name)
        return request(endpoint, cmd="install", path=str(stage / app.name))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def launch(
    endpoint: Endpoint,
    bundle_id: str,
    env: dict[str, str] | None = None,
    args: list[str] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"cmd": "launch", "bundle_id": bundle_id}
    if env:
        fields["env"] = env
    if args:
        fields["args"] = args
    return request(endpoint, **fields)


def terminate(endpoint: Endpoint, bundle_id: str) -> dict[str, Any]:
    return request(endpoint, cmd="terminate", bundle_id=bundle_id)


def screenshot(endpoint: Endpoint, bmp: Path) -> dict[str, Any]:
    return request(endpoint, cmd="screenshot", path=str(bmp))


def shutdown(endpoint: Endpoint) -> None:
    request(endpoint, cmd="shutdown")
