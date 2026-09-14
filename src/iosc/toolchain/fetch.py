import os
from pathlib import Path
import tarfile
from typing import Any
import urllib.request

from iosc.config import paths
from iosc.core import (
    Progress,
    ToolchainError,
    get_reporter,
    sha256_file,
    write_atomic,
)

URL = (
    "https://github.com/llvm/llvm-project/releases/download/"
    "llvmorg-21.1.6/clang%2Bllvm-21.1.6-x86_64-pc-windows-msvc.tar.xz"
)
WANTED_LD64 = "bin/ld64.lld.exe"
WANTED_DSYMUTIL = "bin/dsymutil.exe"
WANTED_LLVM_SYMBOLIZER = "bin/llvm-symbolizer.exe"
# Filled in after the first verified fetch
SHA256_LD64 = ""


class CountingReader:
    def __init__(self, fp: Any, progress: Progress) -> None:
        self.fp = fp
        self.progress = progress
        self.count = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self.fp.read(n)
        if chunk:
            self.count += len(chunk)
            self.progress.update(len(chunk))
        return chunk

    def close(self) -> None:
        self.fp.close()


def _stream_extract(need: dict[str, Path]) -> None:
    reporter = get_reporter()
    reporter.info(f"Source URL {URL}")
    reporter.info(
        "Archive size is roughly 940 MB but transfer aborts early once wanted members land"
    )

    req = urllib.request.Request(URL, headers={"User-Agent": "curl/8"})
    extracted: dict[str, Path] = {}
    with Progress("Fetching linker and debugging tools") as progress:
        with urllib.request.urlopen(req) as resp:
            total_header = resp.headers.get("Content-Length")
            if total_header and total_header.isdigit():
                progress.set_total(int(total_header))
            reader = CountingReader(resp, progress)
            try:
                with tarfile.open(fileobj=reader, mode="r|xz") as tf:
                    for member in tf:
                        tail = member.name.split("/", 1)[-1]
                        if tail in need and member.isfile():
                            src = tf.extractfile(member)
                            if src is not None:
                                write_atomic(need[tail], src.read())
                                extracted[tail] = need[tail]
                        if len(extracted) == len(need):
                            break
            finally:
                reader.close()

    missing = [member for member in need if member not in extracted]
    if missing:
        raise ToolchainError(f"{', '.join(missing)} not found in archive")


# stream till we have ld64.lld, dsymutil, and llvm-symbolizer
def fetch_tools(force: bool = False) -> dict[str, Path]:
    wanted = {
        WANTED_LD64: paths.ld64_lld().path,
        WANTED_DSYMUTIL: paths.dsymutil_tool().path,
        WANTED_LLVM_SYMBOLIZER: paths.llvm_symbolizer_tool().path,
    }
    need = {member: dest for member, dest in wanted.items() if force or not dest.exists()}
    if need:
        _stream_extract(need)

    linker = wanted[WANTED_LD64]
    if SHA256_LD64 and WANTED_LD64 in need:
        digest = sha256_file(linker)
        if digest.lower() != SHA256_LD64.lower():
            try:
                os.remove(linker)
            except OSError:
                pass
            raise ToolchainError(
                f"SHA256 mismatch for {linker.name}. Expected {SHA256_LD64}, got {digest}"
            )

    return {
        "linker": linker,
        "dsymutil": wanted[WANTED_DSYMUTIL],
        "llvm_symbolizer": wanted[WANTED_LLVM_SYMBOLIZER],
    }
