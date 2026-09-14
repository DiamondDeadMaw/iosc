import hashlib
import json
import os
from pathlib import Path

from iosc.core.errors import SdkError
from iosc.core.fsutil import long_path

__all__ = ["verify", "CANARY_PREFIX", "MANIFEST_NAME"]

CANARY_PREFIX = b"NULLcanary"
MANIFEST_NAME = "sdk_manifest.jsonl"


def verify(root: Path | str, manifest_path: Path | str | None = None) -> tuple[int, list[str]]:
    root_path = Path(root).resolve()
    if manifest_path is None:
        manifest_file = root_path / MANIFEST_NAME
    else:
        manifest_file = Path(manifest_path).resolve()

    if not manifest_file.exists():
        raise SdkError(f"No manifest at {manifest_file}")

    checked = 0
    problems: list[str] = []

    with open(manifest_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                problems.append("manifest has a truncated final line")
                break

            if "key" in rec and "payload" in rec:
                payload = rec["payload"]
                if not isinstance(payload, dict):
                    continue
                kind = payload.get("k") or payload.get("kind")
                if kind != "f" and kind != "file":
                    continue
                rel_path = rec["key"]
                expected_size = payload.get("s", payload.get("size"))
                expected_hash = payload.get("h", payload.get("sha256"))
            elif rec.get("k") == "f":
                rel_path = rec.get("p", "")
                expected_size = rec.get("s")
                expected_hash = rec.get("h")
            else:
                continue

            target_path = root_path / Path(rel_path)
            target_str = long_path(target_path)
            try:
                actual_size = os.path.getsize(target_str)
            except OSError:
                problems.append(f"missing: {rel_path}")
                continue

            digest = hashlib.sha256()
            try:
                with open(target_str, "rb") as f:
                    first_10 = f.read(len(CANARY_PREFIX))
                    if first_10 == CANARY_PREFIX:
                        problems.append(f"canary file: {rel_path}")
                    if actual_size != expected_size:
                        problems.append(f"size {actual_size} != {expected_size}: {rel_path}")
                        continue
                    digest.update(first_10)
                    while chunk := f.read(1 << 20):
                        digest.update(chunk)
            except OSError as err:
                problems.append(f"read error on {rel_path}: {err}")
                continue

            if digest.hexdigest() != expected_hash:
                problems.append(f"sha256 mismatch: {rel_path}")
            checked += 1

    return checked, problems
