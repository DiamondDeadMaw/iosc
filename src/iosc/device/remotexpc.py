import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from iosc.core import process
from iosc.core.errors import DeviceError, MissingExternalAsset

TUNNELD_HOST = "127.0.0.1"
TUNNELD_PORT = 49151
INSTALL_HINT = "pip install pymobiledevice3"

# on ios 17+, launch, unified log and crash services sit behind
# RemoteServiceDiscovery, reachable only through this tunnel daemon
TUNNEL_START_TIMEOUT = 15.0
TUNNEL_POLL_INTERVAL = 0.5


def is_available() -> bool:
    return importlib.util.find_spec("pymobiledevice3") is not None


def ensure_available() -> None:
    if not is_available():
        raise MissingExternalAsset(
            "pymobiledevice3 is not installed, needed for launch, unified log "
            f"and crash retrieval. Install it with '{INSTALL_HINT}'."
        )


# the repo root shadows pymobiledevice3 submodules (lockdown.py, afc.py, device.py)
# never let a pymobiledevice3 subprocess inherit it as cwd
def neutral_cwd() -> str:
    return os.environ.get("TEMP") or tempfile.gettempdir()


def run_pmd(
    args: list[str], timeout: float | None = None, check: bool = True
) -> process.CompletedProcess:
    ensure_available()
    argv = [sys.executable, "-m", "pymobiledevice3", *args]
    return process.run(argv, cwd=neutral_cwd(), timeout=timeout, check=check)


# hit the tunneld http api directly
# probe works even when the library isnt importable
def tunnel_running(host: str = TUNNELD_HOST, port: int = TUNNELD_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}", timeout=1.0) as response:
            json.loads(response.read())
        return True
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False


class Tunnel:
    def __init__(
        self,
        host: str = TUNNELD_HOST,
        port: int = TUNNELD_PORT,
        userspace: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.userspace = userspace
        self._process: subprocess.Popen | None = None
        self.owned = False

    def __enter__(self) -> "Tunnel":
        if tunnel_running(self.host, self.port):
            self.owned = False
            return self

        ensure_available()
        argv = [
            sys.executable,
            "-m",
            "pymobiledevice3",
            "remote",
            "tunneld",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if self.userspace:
            argv.append("--userspace")
        self._process = process.spawn_background(argv, cwd=neutral_cwd())
        self.owned = True
        self._wait_until_up()
        return self

    def _wait_until_up(self) -> None:
        deadline = time.monotonic() + TUNNEL_START_TIMEOUT
        while time.monotonic() < deadline:
            if tunnel_running(self.host, self.port):
                return
            if self._process is not None and self._process.poll() is not None:
                raise DeviceError(
                    "pymobiledevice3 remote tunneld exited before coming up"
                )
            time.sleep(TUNNEL_POLL_INTERVAL)
        self.stop()
        raise DeviceError("pymobiledevice3 remote tunneld did not come up in time")

    def stop(self) -> None:
        if not self.owned or self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False
