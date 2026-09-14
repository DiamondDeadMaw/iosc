from dataclasses import dataclass
import json
import sys
from typing import Iterator

from iosc.core import process
from iosc.device import remotexpc


@dataclass(frozen=True)
class OsLogEntry:
    timestamp: str
    level: str
    pid: int
    process_name: str
    message: str
    subsystem: str | None
    category: str | None


# pymobiledevice3 syslog live --format json emits one json object per line
# skip an unparseable line
def _entry_from_json(line: str) -> OsLogEntry | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    label = raw.get("label") or {}
    filename = raw.get("filename") or ""
    return OsLogEntry(
        timestamp=raw.get("timestamp", ""),
        level=raw.get("level", ""),
        pid=raw.get("pid", -1),
        process_name=filename.rsplit("/", 1)[-1],
        message=raw.get("message", ""),
        subsystem=label.get("subsystem"),
        category=label.get("category"),
    )


def format_entry(entry: OsLogEntry) -> str:
    label = ""
    if entry.subsystem or entry.category:
        label = f" [{entry.subsystem}][{entry.category}]"
    return (
        f"{entry.timestamp} {entry.process_name}[{entry.pid}] "
        f"<{entry.level}>: {entry.message}{label}"
    )


def stream_oslog(
    udid: str,
    pid: int | None = None,
    process_name: str | None = None,
    match: list[str] | None = None,
) -> Iterator[OsLogEntry]:
    remotexpc.ensure_available()
    argv = [
        sys.executable,
        "-m",
        "pymobiledevice3",
        "syslog",
        "live",
        "--format",
        "json",
        "--tunnel",
        udid,
    ]
    if pid is not None:
        argv += ["--pid", str(pid)]
    if process_name is not None:
        argv += ["--process-name", process_name]
    for expression in match or []:
        argv += ["--match", expression]

    with remotexpc.Tunnel():
        for line in process.iter_lines(argv, cwd=remotexpc.neutral_cwd()):
            entry = _entry_from_json(line)
            if entry is not None:
                yield entry
