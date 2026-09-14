from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from iosc.core import BuildError, get_reporter, sha256_file, write_atomic

CACHE_VERSION = 1
CACHE_NAME = ".iosc-cache.json"


@dataclass(frozen=True)
class BuildLayout:
    project: Path
    root: Path
    obj: Path
    resources: Path
    staging: Path
    app: Path
    ipa: Path
    cache: Path


def layout_for(project: Path | str, app_name: str) -> BuildLayout:
    project_dir = Path(project).resolve()
    root = project_dir / "build"
    return BuildLayout(
        project=project_dir,
        root=root,
        obj=root / "obj",
        resources=root / "resources",
        staging=root / "staging",
        app=root / f"{app_name}.app",
        ipa=root / f"{app_name}.ipa",
        cache=root / CACHE_NAME,
    )


def fingerprint(parts: dict[str, Any]) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def relative_key(path: Path, project: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


# a stage whose outputs depend on its inputs cant declare them up front
# it records what it produced. next plan declares that list
def write_output_manifest(path: Path, project: Path, produced: Sequence[Path]) -> None:
    keys = sorted(relative_key(p, project) for p in produced)
    write_atomic(path, (json.dumps(keys, indent=2) + "\n").encode("utf-8"))


def read_output_manifest(path: Path, project: Path) -> list[Path]:
    if not Path(path).is_file():
        return []
    try:
        keys = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(keys, list):
        return []
    return [Path(project) / k for k in keys if isinstance(k, str)]


# stages exchange data through declared files, never memory
# a skipped stage still leaves its outputs on disk
@dataclass(frozen=True)
class Stage:
    name: str
    run: Callable[[], None]
    fingerprint: str
    depends_on: tuple[str, ...] = ()
    inputs: tuple[Path, ...] = ()
    outputs: tuple[Path, ...] = ()
    # these files come from an earlier stage and arent known at plan time
    # named here, resolved just before this stage is judged
    resolve_inputs: Callable[[], Sequence[Path]] | None = None


def _resolved(stage: Stage) -> Stage:
    if stage.resolve_inputs is None:
        return stage
    return replace(stage, inputs=tuple(stage.resolve_inputs()))


@dataclass(frozen=True)
class StageOutcome:
    name: str
    ran: bool
    reason: str


@dataclass(frozen=True)
class BuildReport:
    outcomes: tuple[StageOutcome, ...] = field(default_factory=tuple)

    @property
    def ran(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.outcomes if o.ran)

    @property
    def skipped(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.outcomes if not o.ran)


def order_stages(stages: Sequence[Stage]) -> list[Stage]:
    by_name: dict[str, Stage] = {}
    for stage in stages:
        if stage.name in by_name:
            raise BuildError(f"duplicate stage name {stage.name}")
        by_name[stage.name] = stage
    for stage in stages:
        for dep in stage.depends_on:
            if dep not in by_name:
                raise BuildError(f"stage {stage.name} depends on unknown stage {dep}")

    ordered: list[Stage] = []
    placed: set[str] = set()
    remaining = list(stages)
    while remaining:
        ready = [s for s in remaining if all(d in placed for d in s.depends_on)]
        if not ready:
            names = ", ".join(sorted(s.name for s in remaining))
            raise BuildError(f"stage dependency cycle among {names}")
        for stage in ready:
            ordered.append(stage)
            placed.add(stage.name)
        remaining = [s for s in remaining if s.name not in placed]
    return ordered


def _prior_sha(recorded: dict[str, Any], key: str) -> str | None:
    entry = recorded.get(key)
    if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str):
        return entry[0]
    return None


class StageCache:
    def __init__(self, path: Path, project: Path, identity: str) -> None:
        self.path = Path(path)
        self.project = Path(project).resolve()
        self.identity = identity
        self.entries: dict[str, Any] = {}
        self.restored = 0

    # project-relative keys survive moving the project
    def key(self, path: Path | str) -> str:
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(self.project).as_posix()
        except ValueError:
            return resolved.as_posix()

    def _reset(self, reason: str) -> None:
        get_reporter().warn(f"{reason}, rebuilding everything")
        self.entries = {}
        self.restored = 0

    def load(self) -> None:
        self.entries = {}
        self.restored = 0
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            self._reset(f"build cache at {self.path} is unreadable")
            return
        if not isinstance(data, dict):
            self._reset(f"build cache at {self.path} is malformed")
            return
        if data.get("version") != CACHE_VERSION:
            self._reset(
                f"build cache version {data.get('version')} is not {CACHE_VERSION}"
            )
            return
        if data.get("identity") != self.identity:
            self._reset("toolchain or sdk changed since the last build")
            return
        stages = data.get("stages")
        if not isinstance(stages, dict):
            self._reset(f"build cache at {self.path} has no stage table")
            return
        self.entries = stages
        self.restored = len(stages)

    def save(self) -> None:
        payload = {
            "version": CACHE_VERSION,
            "identity": self.identity,
            "stages": self.entries,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        write_atomic(self.path, text.encode("utf-8"))

    def discard(self) -> None:
        self.entries = {}
        self.restored = 0
        if self.path.exists():
            self.path.unlink()

    def drop(self, name: str) -> None:
        if name in self.entries:
            del self.entries[name]
            self.save()

    # size and mtime match the record means bytes unchanged
    # a no-op build never reads the sources
    def _record_file(self, path: Path, previous: dict[str, Any]) -> list[Any] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        prior = previous.get(self.key(path))
        if (
            isinstance(prior, list)
            and len(prior) == 3
            and prior[1] == st.st_size
            and prior[2] == st.st_mtime_ns
        ):
            return [prior[0], st.st_size, st.st_mtime_ns]
        return [sha256_file(path), st.st_size, st.st_mtime_ns]

    def _compare(
        self, paths: Sequence[Path], recorded: Any, noun: str
    ) -> str | None:
        if not isinstance(recorded, dict):
            return f"{noun} record malformed"
        seen: set[str] = set()
        for path in paths:
            key = self.key(path)
            seen.add(key)
            expected = _prior_sha(recorded, key)
            if expected is None:
                return f"new {noun} {Path(path).name}"
            current = self._record_file(Path(path), recorded)
            if current is None:
                return f"{noun} missing {Path(path).name}"
            if current[0] != expected:
                return f"{noun} changed {Path(path).name}"
        for key in recorded:
            if key not in seen:
                return f"{noun} removed {key}"
        return None

    def stale_reason(self, stage: Stage) -> str | None:
        entry = self.entries.get(stage.name)
        if not isinstance(entry, dict):
            return "never built"
        if entry.get("fingerprint") != stage.fingerprint:
            return "settings changed"
        reason = self._compare(stage.outputs, entry.get("outputs"), "output")
        if reason is not None:
            return reason
        return self._compare(stage.inputs, entry.get("inputs"), "input")

    def record(self, stage: Stage) -> None:
        prior = self.entries.get(stage.name)
        prior_inputs = prior.get("inputs", {}) if isinstance(prior, dict) else {}

        inputs: dict[str, Any] = {}
        for path in stage.inputs:
            current = self._record_file(Path(path), prior_inputs)
            if current is not None:
                inputs[self.key(path)] = current

        outputs: dict[str, Any] = {}
        for path in stage.outputs:
            current = self._record_file(Path(path), {})
            if current is None:
                raise BuildError(f"stage {stage.name} did not produce {path}")
            outputs[self.key(path)] = current

        self.entries[stage.name] = {
            "fingerprint": stage.fingerprint,
            "inputs": inputs,
            "outputs": outputs,
        }
        self.save()


def execute(
    stages: Sequence[Stage],
    layout: BuildLayout,
    identity: str,
    force: bool = False,
) -> BuildReport:
    ordered = order_stages(stages)
    cache = StageCache(layout.cache, layout.project, identity)
    cache.load()

    reporter = get_reporter()
    reporter.detail(f"restored {cache.restored} stages from {layout.cache}")

    ran: set[str] = set()
    outcomes: list[StageOutcome] = []
    for stage in ordered:
        stage = _resolved(stage)
        if force:
            reason: str | None = "forced"
        else:
            triggered = [d for d in stage.depends_on if d in ran]
            if triggered:
                reason = f"{triggered[0]} ran"
            else:
                reason = cache.stale_reason(stage)

        if reason is None:
            reporter.detail(f"{stage.name} up to date")
            outcomes.append(StageOutcome(stage.name, False, "up to date"))
            continue

        reporter.step(f"{stage.name} ({reason})")
        # drop before the run. an interrupted stage isnt trusted on restart
        cache.drop(stage.name)
        stage.run()
        cache.record(stage)
        ran.add(stage.name)
        outcomes.append(StageOutcome(stage.name, True, reason))

    reporter.info(f"{len(ran)} stages ran, {len(outcomes) - len(ran)} up to date")
    return BuildReport(tuple(outcomes))
