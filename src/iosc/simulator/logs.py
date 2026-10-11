from collections.abc import Iterator
from pathlib import Path
import re
import time

from iosc.simulator.session import wait_for_exit

RE_OS_LOG = re.compile(r"^\[log (\d+)\]")
RE_HOST = re.compile(r"^(\[[a-z]+[^\]]*\]|===)")
POLL = 0.2


class LineFilter:
    # the app's os_log lines and unprefixed guest stdout, continuation lines follow their first line
    def __init__(self, app_pid: int | None, everything: bool) -> None:
        self.app_pid = app_pid
        self.everything = everything
        self.showing = False

    def wanted(self, line: str) -> bool:
        if self.everything:
            return True
        if line[:1].isspace():
            return self.showing
        match = RE_OS_LOG.match(line)
        if match:
            self.showing = self.app_pid is not None and int(match.group(1)) == self.app_pid
        else:
            self.showing = RE_HOST.match(line) is None
        return self.showing


def current_size(log: Path) -> int:
    try:
        return log.stat().st_size
    except OSError:
        return 0


# lines appended after offset, until the simulator exits
def follow(log: Path, offset: int, simulator_pid: int) -> Iterator[str]:
    pending = b""
    with open(log, "rb") as f:
        f.seek(offset)
        while True:
            chunk = f.read(1 << 16)
            if chunk:
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    yield line.rstrip(b"\r").decode("utf-8", errors="replace")
                continue
            if wait_for_exit(simulator_pid, 0):
                return
            time.sleep(POLL)
