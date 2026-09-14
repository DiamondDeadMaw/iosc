import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any

_DEVELOPER_MODE_CACHED: bool | None = None


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _handle_readonly(func: Any, subpath: str, exc_info: Any) -> None:
    try:
        os.chmod(subpath, stat.S_IWRITE | stat.S_IREAD)
        func(subpath)
    except OSError:
        pass


def _is_link_or_reparse(path: Path) -> bool:
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return True
        if hasattr(st, "st_file_attributes") and (
            st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return True
    except OSError:
        pass
    return False


def rmtree_force(path: Path | str) -> None:
    p = Path(path)
    if not p.exists() and not _is_link_or_reparse(p):
        return
    if _is_link_or_reparse(p):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        if p.is_dir():
            os.rmdir(p)
        else:
            p.unlink()
        return
    if p.is_file():
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        p.unlink()
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(p), onexc=lambda f, sp, exc: _handle_readonly(f, sp, None))
    else:
        shutil.rmtree(str(p), onerror=_handle_readonly)


def long_path(path: Path | str) -> str:
    path_str = str(path)
    if len(path_str) > 250:
        if path_str.startswith("\\\\?\\"):
            return path_str
        abs_path = os.path.abspath(path_str)
        if abs_path.startswith("\\\\?\\"):
            return abs_path
        if abs_path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + abs_path[2:]
        return "\\\\?\\" + abs_path
    return path_str


def is_developer_mode() -> bool:
    global _DEVELOPER_MODE_CACHED
    if _DEVELOPER_MODE_CACHED is not None:
        return _DEVELOPER_MODE_CACHED

    with tempfile.TemporaryDirectory() as tmp_dir:
        target = Path(tmp_dir) / "test_target"
        target.write_text("test", encoding="utf-8")
        link = Path(tmp_dir) / "test_link"
        try:
            os.symlink(target, link)
            _DEVELOPER_MODE_CACHED = True
        except OSError:
            _DEVELOPER_MODE_CACHED = False

    return _DEVELOPER_MODE_CACHED


def make_link(src: Path | str, dst: Path | str) -> str:
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if is_developer_mode():
        try:
            os.symlink(
                str(src_path),
                str(dst_path),
                target_is_directory=src_path.is_dir(),
            )
            return "symlink"
        except OSError:
            pass

    if src_path.is_dir():
        import _winapi

        src_abs = str(src_path.resolve())
        dst_abs = str(dst_path.resolve())
        _winapi.CreateJunction(src_abs, dst_abs)
        return "junction"
    else:
        shutil.copy2(str(src_path), str(dst_path))
        return "copy"


def sha256_file(path: Path | str, chunk: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()
