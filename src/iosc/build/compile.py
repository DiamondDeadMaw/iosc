from pathlib import Path

from iosc.build.graph import BuildLayout, Stage, fingerprint, relative_key
from iosc.build.packages import PackageGraph, module_dir, stage_name
from iosc.config.manifest import Manifest, resolve_sources
from iosc.core.progress import Heartbeat
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain.swift import CompileSpec, compile_objects

STAGE_NAME = "swift"
MAIN_FILE = "main.swift"


def object_path(layout: BuildLayout, product_name: str) -> Path:
    return layout.obj / f"{product_name}.o"


def target_triple(manifest: Manifest, arch: str = "arm64") -> str:
    return f"{arch}-apple-ios{manifest.deployment_target}"


def compile_stage(
    manifest: Manifest,
    layout: BuildLayout,
    toolchain: Toolchain,
    sdk_root: Path,
    product_name: str,
    packages: PackageGraph | None = None,
) -> Stage:
    graph = packages if packages is not None else PackageGraph()
    sources = resolve_sources(manifest, layout.project)
    output = object_path(layout, product_name)
    # top level code goes in main.swift, as xcode does
    # else a lone source builds as a script and rejects @main
    parse_as_library = not any(p.name == MAIN_FILE for p in sources)
    spec = CompileSpec(
        sources=sources,
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        extra_flags=list(manifest.swift_flags),
        parse_as_library=parse_as_library,
        include_dirs=[module_dir(layout)] if graph.targets else [],
    )

    # fingerprint the source list and plugin hashes too
    # a deleted source or rebuilt macro changes output without touching a tracked file
    print_ = fingerprint(
        {
            "swift": toolchain.swift_version,
            "target": target_triple(manifest),
            "sdk": sdk_root.name,
            "flags": list(manifest.swift_flags),
            "parse_as_library": parse_as_library,
            "sources": [relative_key(p, layout.project) for p in sources],
            "plugins": sorted(
                (p.module, p.sha256) for p in toolchain.macro_plugins
            ),
            "modules": [t.module for t in graph.targets],
        }
    )

    def run() -> None:
        with Heartbeat(f"compiling {len(sources)} swift sources"):
            compile_objects(toolchain, spec)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        depends_on=tuple(stage_name(t.module) for t in graph.targets),
        inputs=tuple(sources),
        outputs=(output,),
    )
