from pathlib import Path

from iosc.core.errors import DeviceError
from iosc.device import remotexpc


def list_reports(udid: str, remote_path: str = "/", depth: int = 1) -> list[str]:
    argv = [
        "crash",
        "ls",
        "--remote-file",
        remote_path,
        "--depth",
        str(depth),
        "--udid",
        udid,
    ]
    result = remotexpc.run_pmd(argv, check=False)
    if result.returncode != 0:
        raise DeviceError(
            f"listing crash reports failed: {result.stderr or result.stdout}"
        )
    return [line for line in result.stdout.splitlines() if line.strip()]


def pull_reports(
    udid: str,
    dest: str,
    remote_path: str = "/",
    match: str | None = None,
    erase: bool = False,
) -> list[str]:
    Path(dest).mkdir(parents=True, exist_ok=True)
    argv = ["crash", "pull", dest, "--remote-file", remote_path, "--udid", udid]
    if match is not None:
        argv += ["--match", match]
    if erase:
        argv.append("--erase")

    result = remotexpc.run_pmd(argv, check=False)
    if result.returncode != 0:
        raise DeviceError(
            f"pulling crash reports failed: {result.stderr or result.stdout}"
        )
    return sorted(str(path) for path in Path(dest).rglob("*.ips"))
