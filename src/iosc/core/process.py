from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Iterator

from iosc.core.errors import ExternalToolError
from iosc.core.logging import get_reporter


def format_argv(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


# a partial env would drop PATH and SystemRoot
def resolve_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = dict(os.environ)
    merged.update(env)
    return merged


@dataclass
class CompletedProcess:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float


def run(
    argv: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> CompletedProcess:
    reporter = get_reporter()
    reporter.detail(format_argv(argv))
    start_time = time.perf_counter()

    try:
        res = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=resolve_env(env),
            timeout=timeout,
            capture_output=True,
            text=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as err:
        duration = time.perf_counter() - start_time
        reporter.detail(f"Process failed to start in {duration:.3f}s")
        target = argv[0] if argv else "unknown"
        raise ExternalToolError(
            f"Tool path does not exist: {target}",
            argv=argv,
            exit_code=127,
            stderr=str(err),
        ) from err
    except subprocess.TimeoutExpired as err:
        duration = time.perf_counter() - start_time
        reporter.detail(f"Process timed out after {duration:.3f}s")
        err_stderr = ""
        if isinstance(err.stderr, bytes):
            err_stderr = err.stderr.decode("utf-8", errors="replace")
        elif isinstance(err.stderr, str):
            err_stderr = err.stderr
        raise ExternalToolError(
            f"Command timed out after {timeout}s: {format_argv(argv)}",
            argv=argv,
            exit_code=-1,
            stderr=err_stderr,
        ) from err

    duration = time.perf_counter() - start_time
    reporter.detail(f"Process exited with code {res.returncode} in {duration:.3f}s")
    stdout = res.stdout.decode("utf-8", errors="replace")
    stderr = res.stderr.decode("utf-8", errors="replace")

    if check and res.returncode != 0:
        raise ExternalToolError(
            f"Command failed with exit code {res.returncode}: {format_argv(argv)}",
            argv=argv,
            exit_code=res.returncode,
            stderr=stderr,
        )

    return CompletedProcess(
        argv=argv,
        returncode=res.returncode,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
    )

# caller must terminate or kill the returned process, then wait
def spawn_background(
    argv: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    reporter = get_reporter()
    reporter.detail(format_argv(argv))
    try:
        return subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=resolve_env(env),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as err:
        target = argv[0] if argv else "unknown"
        raise ExternalToolError(
            f"Tool path does not exist: {target}",
            argv=argv,
            exit_code=127,
            stderr=str(err),
        ) from err


# for an unbounded live stream like a log tail
# closing the generator stops the process
def iter_lines(
    argv: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    reporter = get_reporter()
    reporter.detail(format_argv(argv))
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=resolve_env(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as err:
        target = argv[0] if argv else "unknown"
        raise ExternalToolError(
            f"Tool path does not exist: {target}",
            argv=argv,
            exit_code=127,
            stderr=str(err),
        ) from err
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run_streaming(
    argv: list[str],
    callback: Callable[[str], None],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> CompletedProcess:
    reporter = get_reporter()
    reporter.detail(format_argv(argv))
    start_time = time.perf_counter()

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=resolve_env(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as err:
        duration = time.perf_counter() - start_time
        reporter.detail(f"Process failed to start in {duration:.3f}s")
        target = argv[0] if argv else "unknown"
        raise ExternalToolError(
            f"Tool path does not exist: {target}",
            argv=argv,
            exit_code=127,
            stderr=str(err),
        ) from err

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def drain_stderr() -> None:
        if proc.stderr is not None:
            stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=drain_stderr)
    stderr_thread.start()

    timed_out = False
    try:
        if proc.stdout is not None:
            for line_bytes in iter(proc.stdout.readline, b""):
                stdout_chunks.append(line_bytes)
                line_str = line_bytes.decode("utf-8", errors="replace")
                callback(line_str)
                if timeout is not None and (time.perf_counter() - start_time) > timeout:
                    timed_out = True
                    proc.kill()
                    break

        remaining = None
        if timeout is not None:
            remaining = max(0.0, timeout - (time.perf_counter() - start_time))
        proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    finally:
        stderr_thread.join()

    duration = time.perf_counter() - start_time
    reporter.detail(f"Process exited with code {proc.returncode} in {duration:.3f}s")
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    if timed_out:
        raise ExternalToolError(
            f"Command timed out after {timeout}s: {format_argv(argv)}",
            argv=argv,
            exit_code=-1,
            stderr=stderr,
        )

    if check and proc.returncode != 0:
        raise ExternalToolError(
            f"Command failed with exit code {proc.returncode}: {format_argv(argv)}",
            argv=argv,
            exit_code=proc.returncode,
            stderr=stderr,
        )

    return CompletedProcess(
        argv=argv,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
    )
