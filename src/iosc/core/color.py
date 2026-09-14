import os
import sys

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"


def _enable_windows_vt() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stderr.isatty():
        return False
    return True


_ENABLED = supports_color()
if _ENABLED:
    _enable_windows_vt()


def paint(text: str, *codes: str) -> str:
    if not _ENABLED:
        return text
    return f"{''.join(codes)}{text}{RESET}"
