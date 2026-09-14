import plistlib
from typing import Any

from iosc.core.errors import PlistError


def read_plist(data: bytes) -> Any:
    try:
        return plistlib.loads(data)
    except Exception as exc:
        raise PlistError(f"Failed to read plist ({exc})") from exc


def write_plist(obj: Any, binary: bool = True) -> bytes:
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    try:
        return plistlib.dumps(obj, fmt=fmt)
    except Exception as exc:
        raise PlistError(f"Failed to write plist ({exc})") from exc
