import json
import os
from pathlib import Path
import tempfile
from typing import Any

from iosc.core.logging import get_reporter


def write_atomic(path: Path | str, data: bytes) -> None:
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        dir=target_path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        temp_file.write(data)
        temp_file.flush()
        temp_file.close()
        os.replace(temp_path, target_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


class Journal:
    def __init__(self, path: Path | str, settings: dict[str, Any]) -> None:
        self.path = Path(path)
        self.settings = settings
        self._entries: dict[str, Any] = {}
        self._restored_count = 0
        self._file: Any = None

    def _start_fresh(self) -> None:
        self.close()
        header = {"header": True, "settings": self.settings}
        header_bytes = (
            json.dumps(header, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        write_atomic(self.path, header_bytes)
        self._entries.clear()
        self._restored_count = 0
        self._file = open(self.path, "a", encoding="utf-8")

    def open(self) -> None:
        self.close()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries.clear()
        self._restored_count = 0

        if not self.path.exists() or self.path.stat().st_size == 0:
            self._start_fresh()
            return

        raw_lines: list[str] = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()

        if not raw_lines:
            self._start_fresh()
            return

        header_line = raw_lines[0].strip()
        try:
            header_data = json.loads(header_line)
            existing_settings = header_data.get("settings")
        except Exception:
            reporter = get_reporter()
            reporter.warn(f"Corrupt journal header at {self.path}. Starting fresh.")
            self._start_fresh()
            return

        if existing_settings != self.settings:
            reporter = get_reporter()
            reporter.warn(
                f"Journal settings mismatch at {self.path}. "
                f"Expected {self.settings}, found {existing_settings}. Starting fresh."
            )
            self._start_fresh()
            return

        valid_entries: list[tuple[str, Any, str]] = []
        dropped_torn = False

        for idx, line in enumerate(raw_lines[1:], start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                key = entry["key"]
                payload = entry.get("payload")
                valid_entries.append((key, payload, stripped))
            except Exception:
                dropped_torn = True

        for key, payload, _ in valid_entries:
            self._entries[key] = payload

        self._restored_count = len(self._entries)

        if dropped_torn:
            header_obj = {"header": True, "settings": self.settings}
            rewritten_lines = [
                json.dumps(header_obj, separators=(",", ":"))
            ]
            for _, _, stripped in valid_entries:
                rewritten_lines.append(stripped)
            content = ("\n".join(rewritten_lines) + "\n").encode("utf-8")
            write_atomic(self.path, content)

        self._file = open(self.path, "a", encoding="utf-8")

    def is_done(self, key: str) -> bool:
        if self._file is None:
            self.open()
        return key in self._entries

    def record(self, key: str, payload: Any) -> None:
        if self._file is None:
            self.open()
        entry = {"key": key, "payload": payload}
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        self._file.write(line)
        self._file.flush()
        self._entries[key] = payload

    def restored_count(self) -> int:
        return self._restored_count

    def summary(self, remaining: int = 0) -> str:
        return f"Restored {self._restored_count}, {remaining} remaining"

    def close(self) -> None:
        if self._file is not None and not self._file.closed:
            self._file.flush()
            self._file.close()
            self._file = None

    def __enter__(self) -> "Journal":
        if self._file is None:
            self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
