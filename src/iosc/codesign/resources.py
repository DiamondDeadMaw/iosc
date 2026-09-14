from collections.abc import Iterator
import hashlib
import os
from pathlib import Path

from iosc.formats.plist import write_plist

RESOURCE_RULES_V1 = {
    "^.*": True,
    "^.*\\.lproj/": {"optional": True, "weight": 1000.0},
    "^.*\\.lproj/locversion.plist$": {"omit": True, "weight": 1100.0},
    "^Base\\.lproj/": {"weight": 1010.0},
    "^version.plist$": True,
}

RESOURCE_RULES_V2 = {
    ".*\\.dSYM($|/)": {"weight": 11.0},
    "^(.*/)?\\.DS_Store$": {"omit": True, "weight": 2000.0},
    "^.*": True,
    "^.*\\.lproj/": {"optional": True, "weight": 1000.0},
    "^.*\\.lproj/locversion.plist$": {"omit": True, "weight": 1100.0},
    "^Base\\.lproj/": {"weight": 1010.0},
    "^Info\\.plist$": {"omit": True, "weight": 20.0},
    "^PkgInfo$": {"omit": True, "weight": 20.0},
    "^embedded\\.provisionprofile$": {"weight": 20.0},
    "^version\\.plist$": {"weight": 20.0},
}

FILES2_OMITTED = ("Info.plist", "PkgInfo")

CODE_SIGNATURE_DIR = "_CodeSignature"
CODE_RESOURCES_NAME = "CodeResources"


def find_bundle_resources(
    bundle_dir: str | Path,
    executable: str,
) -> Iterator[tuple[str, Path]]:
    bundle_path = Path(bundle_dir)
    for dirpath, dirnames, filenames in os.walk(bundle_path):
        if Path(dirpath) == bundle_path:
            dirnames[:] = [d for d in sorted(dirnames) if d != CODE_SIGNATURE_DIR]
        else:
            dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            relative = full.relative_to(bundle_path).as_posix()
            if relative == executable:
                continue
            yield relative, full


def build_code_resources(bundle_dir: str | Path, executable: str) -> bytes:
    files: dict[str, bytes] = {}
    files2: dict[str, dict[str, bytes]] = {}
    for relative, full in find_bundle_resources(bundle_dir, executable):
        with open(full, "rb") as f:
            payload = f.read()
        files[relative] = hashlib.sha1(payload).digest()
        if relative not in FILES2_OMITTED:
            files2[relative] = {"hash2": hashlib.sha256(payload).digest()}
    return write_plist(
        {
            "files": files,
            "files2": files2,
            "rules": RESOURCE_RULES_V1,
            "rules2": RESOURCE_RULES_V2,
        },
        binary=False,
    )
