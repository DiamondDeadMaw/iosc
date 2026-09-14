from dataclasses import dataclass
import json
from typing import Any

from iosc.core.errors import DeviceError
from iosc.device import remotexpc


@dataclass(frozen=True)
class LaunchResult:
    pid: int | None
    raw_output: str


# TODO: coredevice launch response shape needs checking
# walk the tree for a processIdentifier key rather than a fixed path
def _find_pid(value: Any) -> int | None:
    if isinstance(value, dict):
        if "processIdentifier" in value:
            return value["processIdentifier"]
        for nested in value.values():
            found = _find_pid(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_pid(item)
            if found is not None:
                return found
    return None


def launch_app(
    udid: str,
    bundle_id: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    kill_existing: bool = True,
    suspended: bool = False,
) -> LaunchResult:
    argv = ["developer", "core-device", "launch-application", "--tunnel", udid]
    if not kill_existing:
        argv.append("--no-kill-existing")
    if suspended:
        argv.append("--suspended")
    for key, value in (env or {}).items():
        argv += ["--env", f"{key}={value}"]
    argv.append(bundle_id)
    # command needs one positional arg
    # pass an empty string when the app takes none
    argv += list(args) if args else [""]

    with remotexpc.Tunnel():
        result = remotexpc.run_pmd(argv, check=False)

    if result.returncode != 0:
        raise DeviceError(
            f"launching {bundle_id} failed: {result.stderr or result.stdout}"
        )

    pid = None
    try:
        pid = _find_pid(json.loads(result.stdout))
    except json.JSONDecodeError:
        pass
    return LaunchResult(pid=pid, raw_output=result.stdout)
