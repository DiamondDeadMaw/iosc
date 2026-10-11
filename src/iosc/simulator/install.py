from dataclasses import dataclass
import json
from pathlib import Path
import re

from iosc.config import paths
from iosc.core import MissingExternalAsset, SimulatorError

# control pipe protocol this iosc speaks
PROTOCOL = 1


@dataclass(frozen=True)
class Installation:
    root: Path
    iossim: Path
    runtime: str
    runtime_build: str
    tcc_db: Path


def load() -> Installation:
    asset = paths.simulator_install()
    record = paths.require(asset)
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
        protocol = int(data["protocol"])
        installation = Installation(
            root=Path(data["root"]),
            iossim=Path(data["iossim"]),
            runtime=str(data["runtime"]),
            runtime_build=str(data["runtime_build"]),
            tcc_db=Path(data["tcc_db"]),
        )
    except (ValueError, KeyError, TypeError) as err:
        raise SimulatorError(f"{record} is not a usable simulator install record, {err}") from err
    check_protocol(protocol)
    if not installation.iossim.is_file():
        raise MissingExternalAsset(f"{asset.hint} ({installation.iossim} is missing)")
    return installation


def check_protocol(protocol: int) -> None:
    if protocol != PROTOCOL:
        raise SimulatorError(
            f"the simulator speaks protocol {protocol}, this iosc speaks {PROTOCOL}. "
            "Update whichever is older"
        )


def sdk_version(sdk_root: Path) -> str | None:
    try:
        return str(json.loads((sdk_root / "SDKSettings.json").read_text(encoding="utf-8"))["Version"])
    except (OSError, ValueError, KeyError):
        return None


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))

def runtime_mismatch(sdk_root: Path, installation: Installation) -> str | None:
    sdk = sdk_version(sdk_root)
    if sdk is None or version_key(sdk) <= version_key(installation.runtime):
        return None
    return (
        f"the simulator SDK is {sdk} but the simulator runs iOS {installation.runtime}. "
        "The app can crash at launch on symbols the runtime does not have"
    )
