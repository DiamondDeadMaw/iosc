from dataclasses import asdict, dataclass
import json
from pathlib import Path

from iosc.config.paths import state_dir
from iosc.core import get_reporter, write_atomic


@dataclass(frozen=True)
class Settings:
    swift_toolchain_path: str | None = None
    default_team_id: str | None = None
    default_device_udid: str | None = None
    anisette_provider: str | None = None


def _settings_path(target_path: Path | None = None) -> Path:
    if target_path is not None:
        return Path(target_path)
    return state_dir() / "settings.json"


def load(target_path: Path | None = None) -> Settings:
    resolved = _settings_path(target_path)
    if not resolved.exists():
        return Settings()

    content = resolved.read_bytes()
    if not content.strip():
        return Settings()

    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        return Settings()

    known_keys = {
        "swift_toolchain_path",
        "default_team_id",
        "default_device_udid",
        "anisette_provider",
    }
    reporter = get_reporter()
    valid_args: dict[str, str | None] = {}

    for key, value in data.items():
        if key not in known_keys:
            reporter.warn(f"ignoring unknown settings key '{key}'")
        else:
            valid_args[key] = value

    return Settings(**valid_args)


def save(settings: Settings, target_path: Path | None = None) -> None:
    resolved = _settings_path(target_path)
    data = asdict(settings)
    payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    write_atomic(resolved, payload)
