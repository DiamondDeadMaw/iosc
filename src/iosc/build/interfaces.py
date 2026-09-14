from pathlib import Path

from iosc.build.graph import (
    BuildLayout,
    Stage,
    fingerprint,
    read_output_manifest,
    relative_key,
    write_output_manifest,
)
from iosc.config.manifest import Manifest
from iosc.core import ensure_dir, get_reporter
from iosc.core.errors import BuildError
from iosc.formats import storyboard, xib

STAGE_NAME = "interfaces"
PRODUCED_NAME = "interfaces-outputs.json"
LPROJ_SUFFIX = ".lproj"

XIB_SUFFIX = ".xib"
STORYBOARD_SUFFIX = ".storyboard"
INTERFACE_SUFFIXES = (XIB_SUFFIX, STORYBOARD_SUFFIX)


BASE_LPROJ = "base.lproj"


def find_sources(manifest: Manifest, project: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in manifest.resources:
        for match in project.glob(pattern):
            if match.is_file() and match.suffix.lower() in INTERFACE_SUFFIXES:
                found.add(match)
    return resolve_precedence(sorted(found))


# lproj the source sits in, lowercased. "" if loose
def language_of(source: Path) -> str:
    parent = source.parent.name
    return parent.lower() if parent.lower().endswith(LPROJ_SUFFIX) else ""


# Base.lproj holds the real interface. other lprojs only strings
# a loose copy of a Base name is stale. Base wins
# two files in the same lproj slot fail the build
def resolve_precedence(sources: list[Path]) -> list[Path]:
    by_slot: dict[tuple[str, str], list[Path]] = {}
    for source in sources:
        by_slot.setdefault((language_of(source), source.name.lower()), []).append(source)

    for slot, paths in by_slot.items():
        if len(paths) > 1:
            names = ", ".join(str(p) for p in paths)
            raise BuildError(f"interface {slot[1]!r} is defined more than once: {names}")

    kept: list[Path] = []
    for (language, name), paths in by_slot.items():
        if language == "" and (BASE_LPROJ, name) in by_slot:
            get_reporter().detail(
                f"{paths[0].name} is taken from Base.lproj, ignoring the loose copy")
            continue
        kept.extend(paths)
    return sorted(kept)


# an interface in an lproj keeps that language
# loose ones go to the resources root where UINibName finds them
def destination_dir(source: Path, resources_dir: Path) -> Path:
    parent = source.parent.name
    if parent.lower().endswith(LPROJ_SUFFIX):
        return resources_dir / parent
    return resources_dir


def compile_sources(sources: list[Path], resources_dir: Path) -> list[Path]:
    written: list[Path] = []
    for source in sources:
        target_dir = destination_dir(source, resources_dir)
        ensure_dir(target_dir)
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == XIB_SUFFIX:
            destination = target_dir / f"{source.stem}.nib"
            destination.write_bytes(xib.compile_xib(text))
            written.append(destination)
            continue
        bundle_dir = target_dir / f"{source.stem}.storyboardc"
        names = storyboard.write_storyboardc(text, str(bundle_dir))
        written.extend(bundle_dir / name for name in names)
    return sorted(written)


# a Main Interface in the manifest must name an interface we compile
# otherwise the app launches at a missing nib
def validate_main_interface(manifest: Manifest, sources: list[Path]) -> None:
    stems = {XIB_SUFFIX: set(), STORYBOARD_SUFFIX: set()}
    for source in sources:
        stems[source.suffix.lower()].add(source.stem)
    checks = [
        ("UIMainStoryboardFile", STORYBOARD_SUFFIX, ".storyboard"),
        ("NSMainNibFile", XIB_SUFFIX, ".xib"),
    ]
    for key, suffix, label in checks:
        name = manifest.info_plist.get(key)
        if name and name not in stems[suffix]:
            raise BuildError(f"{key} {name!r} has no matching {label} in resources")


def interfaces_stage(
    manifest: Manifest,
    layout: BuildLayout,
    sources: list[Path] | None = None,
) -> Stage:
    resolved = sources if sources is not None else find_sources(manifest, layout.project)
    validate_main_interface(manifest, resolved)
    produced_path = layout.root / PRODUCED_NAME

    print_ = fingerprint(
        {"sources": [relative_key(p, layout.project) for p in resolved]}
    )

    def run() -> None:
        written = compile_sources(resolved, layout.resources)
        write_output_manifest(produced_path, layout.project, written)
        get_reporter().detail(f"compiled {len(resolved)} interface files")

    outputs = [produced_path]
    outputs.extend(read_output_manifest(produced_path, layout.project))

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        inputs=tuple(resolved),
        outputs=tuple(outputs),
    )
