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
from iosc.formats import strings

STAGE_NAME = "strings"
PRODUCED_NAME = "strings-outputs.json"
LPROJ_SUFFIX = ".lproj"

STRINGS_SUFFIXES = (".strings", ".stringsdict")
CATALOG_SUFFIX = ".xcstrings"


def find_sources(manifest: Manifest, project: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in manifest.resources:
        for match in project.glob(pattern):
            if not match.is_file():
                continue
            suffix = match.suffix.lower()
            if suffix in STRINGS_SUFFIXES or suffix == CATALOG_SUFFIX:
                found.add(match)
    return sorted(found)


# a .strings in an lproj keeps that language
# a loose one is the dev language, goes to Base.lproj
def destination_for(source: Path, resources_dir: Path) -> Path:
    parent = source.parent.name
    language = parent if parent.lower().endswith(LPROJ_SUFFIX) else f"Base{LPROJ_SUFFIX}"
    return resources_dir / language / f"{source.stem}.strings"


def compile_sources(sources: list[Path], resources_dir: Path) -> list[Path]:
    written: list[Path] = []
    for source in sources:
        suffix = source.suffix.lower()
        if suffix == CATALOG_SUFFIX:
            produced = strings.emit_lproj_from_xcstrings(
                str(source), str(resources_dir), table_name=source.stem
            )
            written.extend(Path(p) for p in produced)
            continue
        destination = destination_for(source, resources_dir)
        ensure_dir(destination.parent)
        if suffix == ".stringsdict":
            strings.compile_plist_source(str(source), str(destination))
        else:
            strings.compile_strings_file(str(source), str(destination))
        written.append(destination)
    return sorted(written)


def localization_stage(
    manifest: Manifest,
    layout: BuildLayout,
    sources: list[Path] | None = None,
) -> Stage:
    resolved = sources if sources is not None else find_sources(manifest, layout.project)
    produced_path = layout.root / PRODUCED_NAME

    print_ = fingerprint(
        {"sources": [relative_key(p, layout.project) for p in resolved]}
    )

    def run() -> None:
        written = compile_sources(resolved, layout.resources)
        write_output_manifest(produced_path, layout.project, written)
        get_reporter().detail(f"compiled {len(written)} localization tables")

    outputs = [produced_path]
    outputs.extend(read_output_manifest(produced_path, layout.project))

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        inputs=tuple(resolved),
        outputs=tuple(outputs),
    )
