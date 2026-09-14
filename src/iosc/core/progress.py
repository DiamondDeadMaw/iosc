import sys
import threading
import time
from typing import Any

from iosc.core.logging import get_reporter


def _output_wanted() -> bool:
    reporter = get_reporter()
    if reporter.json_mode:
        return False
    return sys.stderr.isatty()


class Heartbeat:
    def __init__(self, label: str, interval: float = 0.5) -> None:
        self.label = label
        self.interval = interval
        self.start_time = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def is_enabled(self) -> bool:
        return _output_wanted()

    def elapsed(self) -> float:
        return max(0.0, time.perf_counter() - self.start_time)

    def _write(self, text: str, newline: bool = False) -> None:
        suffix = "\n" if newline else ""
        get_reporter().write_stderr(f"\r{text:<79}{suffix}")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._write(f"{self.label} {self.elapsed():.0f}s")

    def __enter__(self) -> "Heartbeat":
        self.start_time = time.perf_counter()
        if self.is_enabled():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 4)
            self._thread = None
        if self.is_enabled():
            outcome = "failed after" if exc_type is not None else "took"
            self._write(f"{self.label} {outcome} {self.elapsed():.1f}s", newline=True)


class Progress:
    def __init__(self, label: str, total: int | None = None) -> None:
        self.label = label
        self.total = total
        self.current = 0
        self.start_time = time.perf_counter()
        self.last_render_time = 0.0

    def is_enabled(self) -> bool:
        return _output_wanted()

    def _format_message(self, now: float) -> str:
        elapsed = max(0.0, now - self.start_time)
        rate = (self.current / elapsed) if elapsed > 0.0 else 0.0
        if self.total is not None and self.total > 0:
            pct = (self.current / self.total) * 100.0
            count_part = f"{self.current}/{self.total} ({pct:.1f}%)"
        else:
            count_part = f"{self.current}"
        return f"{self.label} {count_part} in {elapsed:.1f}s ({rate:.1f}/s)"

    def _render(self, force: bool = False) -> None:
        if not self.is_enabled():
            return
        now = time.perf_counter()
        if not force and (now - self.last_render_time) < 0.1:
            return
        self.last_render_time = now
        msg = self._format_message(now)
        reporter = get_reporter()
        reporter.write_stderr(f"\r{msg:<79}")

    def update(self, n: int = 1) -> None:
        self.current += n
        self._render()

    def set_total(self, n: int) -> None:
        self.total = n
        self._render()

    # for work that reports absolute position, not an increment
    def set_current(self, n: int) -> None:
        self.current = n
        self._render()

    def __enter__(self) -> "Progress":
        self.start_time = time.perf_counter()
        self.last_render_time = 0.0
        self._render(force=True)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self.is_enabled():
            return
        now = time.perf_counter()
        msg = self._format_message(now)
        reporter = get_reporter()
        reporter.write_stderr(f"\r{msg:<79}\n")
