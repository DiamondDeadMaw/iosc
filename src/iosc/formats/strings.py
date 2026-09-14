import json
import os
import re

from iosc.core.errors import StringsError
from iosc.formats.plist import read_plist, write_plist

STRINGS_PAIR_RE = re.compile(
    r'"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;'
)
STRINGS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def parse_strings_text(text: str) -> dict[str, str]:
    stripped = STRINGS_COMMENT_RE.sub("", text)
    result: dict[str, str] = {}
    for match in STRINGS_PAIR_RE.finditer(stripped):
        key = _unescape(match.group(1))
        value = _unescape(match.group(2))
        result[key] = value
    return result


def compile_strings_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "r", encoding="utf-8") as f:
        text = f.read()
    table = parse_strings_text(text)
    data = write_plist(table, binary=True)
    with open(dst_path, "wb") as f:
        f.write(data)


def compile_plist_source(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as f:
        raw = f.read()
    data = read_plist(raw)
    out = write_plist(data, binary=True)
    with open(dst_path, "wb") as f:
        f.write(out)


def parse_xcstrings(path: str) -> tuple[str, dict[str, dict[str, str]]]:
    with open(path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    source_lang = catalog.get("sourceLanguage")
    if not source_lang:
        raise StringsError(f"{path} has no sourceLanguage")

    by_lang: dict[str, dict[str, str]] = {}
    strings = catalog.get("strings", {})

    for key, entry in strings.items():
        if entry.get("shouldTranslate") is False:
            continue
        localizations = entry.get("localizations", {})
        for lang, loc in localizations.items():
            unit = loc.get("stringUnit")
            if unit is None:
                continue
            value = unit.get("value")
            if value is None:
                continue
            by_lang.setdefault(lang, {})[key] = value

    return source_lang, by_lang


def emit_lproj_from_xcstrings(
    xcstrings_path: str,
    output_dir: str,
    table_name: str = "Localizable",
) -> list[str]:
    _source_lang, by_lang = parse_xcstrings(xcstrings_path)
    written: list[str] = []
    for lang, table in by_lang.items():
        lproj_dir = os.path.join(output_dir, f"{lang}.lproj")
        os.makedirs(lproj_dir, exist_ok=True)
        dst = os.path.join(lproj_dir, f"{table_name}.strings")
        data = write_plist(table, binary=True)
        with open(dst, "wb") as f:
            f.write(data)
        written.append(dst)
    return written
