from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable

from iosc.config import paths
from iosc.core.checkpoint import Journal, write_atomic
from iosc.core.errors import SdkError
from iosc.core.fsutil import (
    ensure_dir,
    is_developer_mode,
    long_path,
    make_link,
    rmtree_force,
)
from iosc.core.logging import get_reporter
from iosc.core.progress import Progress
from iosc.sdk import xip
from iosc.sdk.verify import CANARY_PREFIX, MANIFEST_NAME, verify

__all__ = [
    "ExtractStats",
    "CaseCollisionError",
    "InvalidPathError",
    "SymlinkResolutionError",
    "detect_symlink_tier",
    "parse_carve_path",
    "resolve_link",
    "validate_path_segments",
    "to_win32_path",
    "extract",
    "verify",
    "CANARY_PREFIX",
    "CARVE_VERSION",
    "MANIFEST_NAME",
    "build_link_plan",
]

SWIFT_VERSION_RE = re.compile(r"^swift-\d")
ALLOWED_PLATFORM_SUBS = (
    "SDKs",
    "Library/Frameworks",
    "Library/PrivateFrameworks",
    "usr/lib",
)
INVALID_FILENAME_CHARS = set('<>:"|?*')
MAX_LINK_HOPS = 40
CARVE_VERSION = 4


class CaseCollisionError(SdkError):
    pass


class InvalidPathError(SdkError):
    pass


class SymlinkResolutionError(SdkError):
    pass


@dataclass(frozen=True)
class ExtractStats:
    files_written: int = 0
    dirs_created: int = 0
    symlinks_created: int = 0
    symlinks_degraded: int = 0
    symlinks_broken: int = 0
    bytes_written: int = 0
    entries_seen: int = 0
    entries_skipped: int = 0
    files_resumed: int = 0
    links_resumed: int = 0
    bytes_resumed: int = 0


@dataclass
class _MutableStats:
    files_written: int = 0
    dirs_created: int = 0
    symlinks_created: int = 0
    symlinks_degraded: int = 0
    symlinks_broken: int = 0
    bytes_written: int = 0
    entries_seen: int = 0
    entries_skipped: int = 0
    files_resumed: int = 0
    links_resumed: int = 0
    bytes_resumed: int = 0

    def freeze(self) -> ExtractStats:
        return ExtractStats(
            files_written=self.files_written,
            dirs_created=self.dirs_created,
            symlinks_created=self.symlinks_created,
            symlinks_degraded=self.symlinks_degraded,
            symlinks_broken=self.symlinks_broken,
            bytes_written=self.bytes_written,
            entries_seen=self.entries_seen,
            entries_skipped=self.entries_skipped,
            files_resumed=self.files_resumed,
            links_resumed=self.links_resumed,
            bytes_resumed=self.bytes_resumed,
        )


def to_win32_path(path: str | Path) -> str:
    return long_path(path)


def validate_path_segments(rel_path: str) -> None:
    segments = rel_path.replace("\\", "/").split("/")
    for seg in segments:
        if not seg:
            continue
        for ch in seg:
            if ch in INVALID_FILENAME_CHARS:
                raise InvalidPathError(
                    f"Path '{rel_path}' contains illegal character '{ch}' in segment '{seg}'"
                )
        if seg.endswith("."):
            raise InvalidPathError(
                f"Path '{rel_path}' contains trailing dot in segment '{seg}'"
            )
        if seg.endswith(" "):
            raise InvalidPathError(
                f"Path '{rel_path}' contains trailing space in segment '{seg}'"
            )


def detect_symlink_tier(probe_dir: Path | str | None = None) -> int:
    return 1 if is_developer_mode() else 2


def _get_default_platforms() -> tuple[str, ...]:
    return paths.carve_platforms()


def parse_carve_path(name: str, allowed_platforms: tuple[str, ...] | None = None) -> str | None:
    if allowed_platforms is None:
        allowed_platforms = _get_default_platforms()

    p = name.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    elif p.startswith("/"):
        p = p[1:]

    parts = p.split("/")
    if not parts or not parts[0].endswith(".app"):
        return None

    if len(parts) < 3 or parts[1] != "Contents" or parts[2] != "Developer":
        return None

    rest_parts = parts[3:]
    if not rest_parts or rest_parts == [""]:
        return None

    if "prebuilt-modules" in rest_parts:
        return None

    for i in range(len(rest_parts) - 2):
        if (
            rest_parts[i] == "usr"
            and rest_parts[i + 1] == "share"
            and rest_parts[i + 2] == "man"
        ):
            return None

    rel_path = "/".join(rest_parts)

    if (
        len(rest_parts) == 3
        and rest_parts[0] == "Toolchains"
        and rest_parts[1] == "XcodeDefault.xctoolchain"
        and rest_parts[2] == "ToolchainInfo.plist"
    ):
        return rel_path

    primary, sim, _ = allowed_platforms if len(allowed_platforms) >= 2 else (_get_default_platforms()[:2] + ("",))
    if (
        len(rest_parts) >= 5
        and rest_parts[0] == "Toolchains"
        and rest_parts[1] == "XcodeDefault.xctoolchain"
        and rest_parts[2] == "usr"
        and rest_parts[3] == "lib"
    ):
        sub = rest_parts[4]
        if sub in ("swift", "swift_static", "clang"):
            return rel_path
        ios_slices = (primary.lower(), sim.lower())
        if (
            SWIFT_VERSION_RE.match(sub)
            and len(rest_parts) >= 6
            and rest_parts[5] in ios_slices
        ):
            return rel_path

    if len(rest_parts) >= 4 and rest_parts[0] == "Platforms":
        plat_comp = rest_parts[1]
        for p_name in allowed_platforms:
            if plat_comp == f"{p_name}.platform":
                if rest_parts[2] == "Developer":
                    sub = "/".join(rest_parts[3:])
                    for plat_sub in ALLOWED_PLATFORM_SUBS:
                        if sub == plat_sub or sub.startswith(plat_sub + "/"):
                            return rel_path
                break

    return None


def resolve_link(link_rel: str, links: dict[str, str]) -> str | None:
    cur: list[str] = []
    pending = link_rel.split("/")
    hops = 0
    while pending:
        comp = pending.pop(0)
        if comp in ("", "."):
            continue
        if comp == "..":
            if not cur:
                return None
            cur.pop()
            continue
        cur.append(comp)
        target = links.get("/".join(cur))
        if target is None:
            continue
        hops += 1
        if hops > MAX_LINK_HOPS:
            raise SymlinkResolutionError(
                f"Symlink '{link_rel}' exceeded {MAX_LINK_HOPS} hops, probable cycle"
            )
        cur.pop()
        if target.startswith("/"):
            return None
        pending = [p for p in target.split("/") if p != ""] + pending
    return "/".join(cur)


MEGABYTE = 1 << 20


# compressed bytes is the only total known up front. both passes measure against it
def megabytes_into(prog: Progress) -> xip.ProgressCallback:
    def advance(done: int, total: int) -> None:
        if prog.total is None:
            prog.set_total(total // MEGABYTE)
        prog.set_current(done // MEGABYTE)

    return advance


def build_link_plan(
    xip_path: str | os.PathLike[str],
    allowed_platforms: tuple[str, ...] | None = None,
) -> dict:
    groups: dict[tuple, dict] = {}
    with Progress("Indexing archive, MB") as prog:
        for entry in xip.iter_entries(xip_path, on_progress=megabytes_into(prog)):
            if entry.nlink <= 1 or entry.kind != "file":
                continue

            key = (entry.dev, entry.ino)
            if key not in groups:
                groups[key] = {
                    "real_entries": [],
                    "destinations": [],
                    "dest_modes": {},
                }

            is_canary = False
            if entry.size >= len(CANARY_PREFIX):
                first_10 = entry._stream.read(len(CANARY_PREFIX))
                is_canary = (first_10 == CANARY_PREFIX)

            carved_rel = parse_carve_path(
                entry.name, allowed_platforms=allowed_platforms
            )
            if is_canary:
                if carved_rel is not None:
                    groups[key]["destinations"].append(carved_rel)
                    groups[key]["dest_modes"][carved_rel] = entry.mode
            else:
                groups[key]["real_entries"].append(
                    (entry.name, carved_rel is not None, entry.size, entry.mode)
                )

    plan: dict[tuple, dict] = {}
    for key, g in groups.items():
        destinations = g["destinations"]
        if not destinations:
            continue

        real_entries = g["real_entries"]
        if len(real_entries) != 1:
            raise SdkError(
                f"Link group {key} needed by {len(destinations)} destinations has {len(real_entries)} real entries, expected 1"
            )

        real_name, real_inside, real_size, real_mode = real_entries[0]
        plan[key] = {
            "real_name": real_name,
            "real_entry": real_name,
            "real_inside_carve": real_inside,
            "inside_carve": real_inside,
            "destinations": destinations,
            "dest_modes": g["dest_modes"],
            "real_size": real_size,
            "real_mode": real_mode,
        }

    return plan


def _on_disk_size(path_str: str) -> int | None:
    try:
        return os.path.getsize(path_str)
    except OSError:
        return None


def extract(
    xip_path: str | os.PathLike[str],
    outdir: str | os.PathLike[str] | None = None,
    progress: Callable[[ExtractStats], None] | None = None,
    resume: bool = True,
    allowed_platforms: tuple[str, ...] | None = None,
) -> ExtractStats:
    reporter = get_reporter()
    stats = _MutableStats()

    if outdir is None:
        outdir = paths.sdk_extract_root().path

    outdir_path = Path(outdir).resolve()
    ensure_dir(outdir_path)

    xip_file = Path(xip_path)
    link_plan = build_link_plan(xip_file, allowed_platforms=allowed_platforms)
    num_groups = len(link_plan)
    total_destinations = sum(len(g["destinations"]) for g in link_plan.values())
    out_of_carve_bytes = sum(
        g["real_size"] for g in link_plan.values() if not g["real_inside_carve"]
    )
    reporter.info(
        f"Link plan: {num_groups:,} groups, {total_destinations:,} destinations, "
        f"{out_of_carve_bytes:,} out-of-carve bytes"
    )

    planned_real_by_name: dict[str, tuple[tuple, dict]] = {
        g["real_name"]: (k, g) for k, g in link_plan.items()
    }
    planned_destinations: set[str] = {
        d for g in link_plan.values() for d in g["destinations"]
    }

    staging_dir = outdir_path / ".link_staging"
    if staging_dir.exists():
        rmtree_force(staging_dir)

    manifest_path = outdir_path / MANIFEST_NAME
    settings = {
        "carve_version": CARVE_VERSION,
        "xip_name": xip_file.name,
        "xip_size": xip_file.stat().st_size,
    }
    journal = Journal(manifest_path, settings)
    if resume:
        journal.open()
    else:
        journal._start_fresh()

    prior = journal._entries
    if prior:
        reporter.info(f"Resuming, {len(prior):,} entries recorded by a previous run.")

    tier = detect_symlink_tier(outdir_path)
    tier_desc = (
        "Tier 1 (os.symlink, Developer Mode active)"
        if tier == 1
        else "Tier 2 (Directory junctions via mklink /J + file copy fallback)"
    )
    reporter.info(f"Active symlink strategy: {tier_desc}")

    seen_paths: dict[str, str] = {}
    created_dirs: set[str] = set()
    created_files: set[str] = set()
    deferred_symlinks: list[tuple[str, str, int]] = []

    # driven by compressed bytes consumed
    # the archive states payload length up front, not the entry count
    with Progress("Extracting SDK, MB") as prog:
        for entry in xip.iter_entries(xip_file, on_progress=megabytes_into(prog)):
            stats.entries_seen += 1
            rel_path = parse_carve_path(entry.name, allowed_platforms=allowed_platforms)

            is_outside_real = False
            planned_group = None
            if entry.name in planned_real_by_name:
                group_key, planned_group = planned_real_by_name[entry.name]
                if not planned_group["real_inside_carve"]:
                    is_outside_real = True

            if rel_path is None and not is_outside_real:
                stats.entries_skipped += 1
                if progress:
                    progress(stats.freeze())
                continue

            if is_outside_real:
                assert planned_group is not None
                ensure_dir(staging_dir)
                dev, ino = group_key
                dev_str = f"{dev[0]}_{dev[1]}" if isinstance(dev, tuple) else str(dev)
                staged_path = staging_dir / f"{dev_str}_{ino}.tmp"
                digest = hashlib.sha256()
                written = 0
                with open(long_path(staged_path), "wb") as fh:
                    for block in entry.chunks():
                        fh.write(block)
                        digest.update(block)
                        written += len(block)
                planned_group["real_staged_path"] = str(staged_path)
                planned_group["hash"] = digest.hexdigest()
                if progress:
                    progress(stats.freeze())
                continue

            validate_path_segments(rel_path)

            norm_lower = rel_path.lower()
            if norm_lower in seen_paths:
                existing = seen_paths[norm_lower]
                if existing != rel_path:
                    raise CaseCollisionError(
                        f"Case collision between '{existing}' and '{rel_path}'"
                    )
            else:
                seen_paths[norm_lower] = rel_path

            if rel_path in planned_destinations:
                if progress:
                    progress(stats.freeze())
                continue

            target_file_path = outdir_path / Path(rel_path)
            win_path = long_path(target_file_path)

            if entry.kind == "dir":
                ensure_dir(target_file_path)
                created_dirs.add(rel_path)
                stats.dirs_created += 1
                if rel_path not in prior:
                    journal.record(rel_path, {"k": "d"})

            elif entry.kind == "file":
                done = prior.get(rel_path)
                if (
                    done is not None
                    and (done.get("k") == "f" or done.get("kind") == "file")
                    and done.get("s", done.get("size")) == entry.size
                    and _on_disk_size(win_path) == entry.size
                ):
                    created_files.add(rel_path)
                    stats.files_resumed += 1
                    stats.bytes_resumed += entry.size
                    if entry.name in planned_real_by_name:
                        _, g = planned_real_by_name[entry.name]
                        g["real_local_path"] = win_path
                        g["hash"] = done.get("h", done.get("sha256"))
                    if progress:
                        progress(stats.freeze())
                    continue

                ensure_dir(target_file_path.parent)
                digest = hashlib.sha256()
                written = 0
                with open(win_path, "wb") as fh:
                    for block in entry.chunks():
                        fh.write(block)
                        digest.update(block)
                        written += len(block)
                stats.bytes_written += written
                created_files.add(rel_path)
                stats.files_written += 1
                if entry.mode & 0o111:
                    try:
                        os.chmod(win_path, 0o755)
                    except OSError:
                        pass
                journal.record(
                    rel_path,
                    {
                        "k": "f",
                        "s": written,
                        "h": digest.hexdigest(),
                        "x": 1 if entry.mode & 0o111 else 0,
                    },
                )
                if entry.name in planned_real_by_name:
                    _, g = planned_real_by_name[entry.name]
                    g["real_local_path"] = win_path
                    g["hash"] = digest.hexdigest()

            elif entry.kind == "symlink":
                raw_target = entry.data().decode("utf-8", errors="replace")
                deferred_symlinks.append((rel_path, raw_target, entry.mode))

            if progress:
                progress(stats.freeze())

    for group_key, group in link_plan.items():
        src_path = group.get("real_local_path") or group.get("real_staged_path")
        if not src_path or not os.path.exists(src_path):
            raise SdkError(
                f"Source file for link group {group_key} ({group['real_name']}) was not found"
            )
        file_hash = group["hash"]
        file_size = group["real_size"]

        for dest_rel in group["destinations"]:
            dest_file_path = outdir_path / Path(dest_rel)
            dest_win = long_path(dest_file_path)
            mode = group["dest_modes"].get(dest_rel, group["real_mode"])

            done = prior.get(dest_rel)
            if (
                done is not None
                and (done.get("k") == "f" or done.get("kind") == "file")
                and done.get("s", done.get("size")) == file_size
                and _on_disk_size(dest_win) == file_size
            ):
                created_files.add(dest_rel)
                stats.files_resumed += 1
                stats.bytes_resumed += file_size
                continue

            ensure_dir(dest_file_path.parent)
            shutil.copyfile(src_path, dest_win)
            if mode & 0o111:
                try:
                    os.chmod(dest_win, 0o755)
                except OSError:
                    pass
            created_files.add(dest_rel)
            stats.files_written += 1
            stats.bytes_written += file_size
            journal.record(
                dest_rel,
                {
                    "k": "f",
                    "s": file_size,
                    "h": file_hash,
                    "x": 1 if mode & 0o111 else 0,
                },
            )

    if staging_dir.exists():
        rmtree_force(staging_dir)

    links = {rel: target for rel, target, _ in deferred_symlinks}
    dir_links: list[tuple[str, str]] = []
    file_links: list[tuple[str, str, int]] = []
    broken: list[tuple[str, str]] = []

    for link_rel, raw_target, mode in deferred_symlinks:
        final = resolve_link(link_rel, links)
        if final in created_dirs:
            dir_links.append((link_rel, final))
        elif final in created_files:
            file_links.append((link_rel, final, mode))
        else:
            broken.append((link_rel, raw_target))
    stats.symlinks_broken = len(broken)

    def link_path(link_rel: str) -> str:
        p = outdir_path / Path(link_rel)
        ensure_dir(p.parent)
        return long_path(p)

    def target_path(final_rel: str) -> str:
        return long_path(outdir_path / Path(final_rel))

    def already_linked(link_rel: str, want: str) -> bool:
        done = prior.get(link_rel)
        if done is None or done.get("k") != "l" or done.get("m") != want:
            return False
        win = link_path(link_rel)
        if want == "copy":
            return _on_disk_size(win) == done.get("s")
        return os.path.isdir(win) if want != "symlink" else os.path.lexists(win)

    if tier == 1:
        dirset = {rel for rel, _ in dir_links}
        for link_rel, raw_target, _ in deferred_symlinks:
            if already_linked(link_rel, "symlink"):
                stats.links_resumed += 1
                continue
            dst_link = link_path(link_rel)
            target_str = raw_target.replace("/", "\\")
            try:
                os.symlink(target_str, dst_link, target_is_directory=link_rel in dirset)
                stats.symlinks_created += 1
                journal.record(
                    link_rel,
                    {"k": "l", "t": raw_target, "m": "symlink"},
                )
            except OSError:
                pass
    else:
        for link_rel, final in sorted(dir_links, key=lambda x: x[0].count("/")):
            if already_linked(link_rel, "junction") or already_linked(link_rel, "copytree"):
                stats.links_resumed += 1
                continue
            win_link = link_path(link_rel)
            win_target = target_path(final)
            mode_name = make_link(src=win_target, dst=win_link)
            if mode_name in ("symlink", "junction"):
                stats.symlinks_created += 1
            else:
                stats.symlinks_degraded += 1
            journal.record(link_rel, {"k": "l", "r": final, "m": mode_name})

        for link_rel, final, mode in file_links:
            if already_linked(link_rel, "copy"):
                stats.links_resumed += 1
                continue
            win_link = link_path(link_rel)
            win_target = target_path(final)
            mode_name = make_link(src=win_target, dst=win_link)
            if mode & 0o111:
                try:
                    os.chmod(win_link, 0o755)
                except OSError:
                    pass
            if mode_name == "symlink":
                stats.symlinks_created += 1
            else:
                stats.symlinks_degraded += 1
            journal.record(
                link_rel,
                {
                    "k": "l",
                    "r": final,
                    "m": mode_name,
                    "s": _on_disk_size(win_link),
                },
            )

    if broken:
        reporter.warn(
            f"{len(broken)} symlinks point outside the carve and were skipped, "
            f"first: {broken[0][0]} -> {broken[0][1]}"
        )

    journal.close()

    frozen_stats = stats.freeze()
    if progress:
        progress(frozen_stats)

    return frozen_stats
