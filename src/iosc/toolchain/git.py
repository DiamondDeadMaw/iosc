from pathlib import Path
import re

from iosc.core import ExternalToolError, PackageError, process

LS_REMOTE_TIMEOUT = 60
# leading v optional, same as swiftpm
RE_RELEASE_TAG = re.compile(r"^refs/tags/v?(\d+)\.(\d+)\.(\d+)$")
# credential prompts fail instead of hanging
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def release_versions(ls_remote: str) -> list[str]:
    found: set[tuple[int, int, int]] = set()
    for line in ls_remote.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        match = RE_RELEASE_TAG.match(parts[1])
        if match:
            found.add((int(match[1]), int(match[2]), int(match[3])))
    return [".".join(str(n) for n in version) for version in sorted(found)]


def latest_release(git: Path, url: str) -> str | None:
    argv = [str(git), "ls-remote", "--tags", "--refs", url]
    try:
        proc = process.run(argv, env=NO_PROMPT, timeout=LS_REMOTE_TIMEOUT)
    except ExternalToolError as err:
        detail = err.stderr.strip() or err.message
        raise PackageError(f"could not list the tags of {url}\n{detail}") from err
    versions = release_versions(proc.stdout)
    return versions[-1] if versions else None
