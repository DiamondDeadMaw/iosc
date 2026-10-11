import json
from pathlib import Path
from typing import Any

from iosc.core import ExternalToolError, PackageError, get_reporter, process
from iosc.toolchain.discovery import Toolchain

RESOLVE_ERROR_LINES = 20


def swift_driver(toolchain: Toolchain) -> Path:
    return toolchain.swift_bin_dir / f"swift{toolchain.swiftc.suffix}"


# scratch keeps swiftpm state out of the users checkout
def package_argv(
    toolchain: Toolchain,
    package_dir: Path,
    scratch: Path,
    subcommand: list[str],
    cache: Path | None = None,
) -> list[str]:
    return [
        str(swift_driver(toolchain)),
        "package",
        "--package-path",
        str(package_dir),
        "--scratch-path",
        str(scratch),
        *(["--cache-path", str(cache)] if cache is not None else []),
        *subcommand,
    ]


def _run_json(
    toolchain: Toolchain,
    package_dir: Path,
    scratch: Path,
    env: dict[str, str],
    subcommand: list[str],
) -> dict[str, Any]:
    label = " ".join(subcommand[:1])
    argv = package_argv(toolchain, package_dir, scratch, subcommand)
    try:
        proc = process.run(argv, env=env)
    except ExternalToolError as err:
        detail = err.stderr.strip() or err.message
        raise PackageError(f"swift package {label} failed for {package_dir}\n{detail}") from err
    try:
        data = json.loads(proc.stdout)
    except ValueError as err:
        raise PackageError(
            f"swift package {label} for {package_dir} did not print JSON"
        ) from err
    if not isinstance(data, dict):
        raise PackageError(f"swift package {label} for {package_dir} printed {type(data).__name__}")
    return data


def _stream(
    toolchain: Toolchain,
    package_dir: Path,
    scratch: Path,
    env: dict[str, str],
    subcommand: list[str],
    cache: Path | None = None,
) -> None:
    reporter = get_reporter()

    def relay(line: str) -> None:
        if line.strip():
            reporter.info(line.rstrip())

    argv = package_argv(toolchain, package_dir, scratch, subcommand, cache)
    try:
        process.run_streaming(argv, relay, env=env, merge_stderr=True)
    except ExternalToolError as err:
        tail = "\n".join(err.stderr.strip().splitlines()[-RESOLVE_ERROR_LINES:])
        raise PackageError(f"swift package {subcommand[0]} failed\n{tail or err.message}") from err


def resolve(
    toolchain: Toolchain,
    package_dir: Path,
    scratch: Path,
    env: dict[str, str],
    cache: Path | None = None,
) -> None:
    _stream(toolchain, package_dir, scratch, env, ["resolve"], cache)


# empty identities updates every package
def update(
    toolchain: Toolchain,
    package_dir: Path,
    scratch: Path,
    env: dict[str, str],
    identities: list[str],
    cache: Path | None = None,
) -> None:
    _stream(toolchain, package_dir, scratch, env, ["update", *identities], cache)


def show_dependencies(
    toolchain: Toolchain, package_dir: Path, scratch: Path, env: dict[str, str]
) -> dict[str, Any]:
    return _run_json(
        toolchain, package_dir, scratch, env, ["show-dependencies", "--format", "json"]
    )


def describe(
    toolchain: Toolchain, package_dir: Path, scratch: Path, env: dict[str, str]
) -> dict[str, Any]:
    return _run_json(toolchain, package_dir, scratch, env, ["describe", "--type", "json"])


def dump_package(
    toolchain: Toolchain, package_dir: Path, scratch: Path, env: dict[str, str]
) -> dict[str, Any]:
    return _run_json(toolchain, package_dir, scratch, env, ["dump-package"])
