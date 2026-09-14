import json
import sys
from typing import Any


class Reporter:
    def __init__(self, verbose: bool = False, json_mode: bool = False) -> None:
        self.verbose = verbose
        self.json_mode = json_mode

    def configure(self, verbose: bool, json_mode: bool) -> None:
        self.verbose = verbose
        self.json_mode = json_mode

    # diagnostics stay on stderr. json mode uses stdout only
    def _emit(self, msg: str, always: bool = False) -> None:
        if self.json_mode and not always:
            return
        sys.stderr.write(f"{msg}\n")
        sys.stderr.flush()

    def info(self, msg: str) -> None:
        self._emit(msg)

    def warn(self, msg: str) -> None:
        self._emit(f"warning {msg}", always=True)

    def error(self, msg: str) -> None:
        self._emit(f"error {msg}", always=True)

    def detail(self, msg: str) -> None:
        if self.verbose:
            self._emit(msg)

    def success(self, msg: str) -> None:
        self._emit(msg)

    def step(self, msg: str) -> None:
        self._emit(f"-> {msg}")

    def result(self, obj: Any) -> None:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def write_stderr(self, text: str) -> None:
        if self.json_mode:
            return
        sys.stderr.write(text)
        sys.stderr.flush()


_REPORTER = Reporter()


def get_reporter() -> Reporter:
    return _REPORTER


def configure(verbose: bool, json_mode: bool) -> None:
    _REPORTER.configure(verbose, json_mode)


def info(msg: str) -> None:
    _REPORTER.info(msg)


def warn(msg: str) -> None:
    _REPORTER.warn(msg)


def error(msg: str) -> None:
    _REPORTER.error(msg)


def detail(msg: str) -> None:
    _REPORTER.detail(msg)


def success(msg: str) -> None:
    _REPORTER.success(msg)


def step(msg: str) -> None:
    _REPORTER.step(msg)


def result(obj: Any) -> None:
    _REPORTER.result(obj)
