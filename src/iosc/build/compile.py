from pathlib import Path

from iosc.build import xcframework
from iosc.build.graph import BuildLayout, Stage, fingerprint, relative_key
from iosc.build.packages import PackageGraph, clang_search, module_dir, stage_name
from iosc.config.manifest import Manifest, resolve_sources
from iosc.core.progress import Heartbeat
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain import swift
from iosc.toolchain.swift import CompileSpec, compile_objects

STAGE_NAME = "swift"
MAIN_FILE = "main.swift"


def object_path(layout: BuildLayout, product_name: str) -> Path:
    return layout.obj / f"{product_name}.o"


def target_triple(manifest: Manifest, arch: str = "arm64", simulator: bool = False) -> str:
    return swift.target_triple(arch, manifest.deployment_target, simulator)


def compile_stage(
    manifest: Manifest,
    layout: BuildLayout,
    toolchain: Toolchain,
    sdk_root: Path,
    product_name: str,
    packages: PackageGraph | None = None,
    simulator: bool = False,
) -> Stage:
    graph = packages if packages is not None else PackageGraph()
    sources = resolve_sources(manifest, layout.project)
    output = object_path(layout, product_name)
    # top level code goes in main.swift, as xcode does
    # else a lone source builds as a script and rejects @main
    parse_as_library = not any(p.name == MAIN_FILE for p in sources)
    clang_includes, clang_flags = clang_search(graph.targets, layout)
    spec = CompileSpec(
        sources=sources,
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        extra_flags=[*manifest.swift_flags, *clang_flags, *xcframework.module_map_flags(graph.binaries)],
        parse_as_library=parse_as_library,
        include_dirs=[
            *([module_dir(layout)] if graph.targets else []),
            *clang_includes,
            *xcframework.include_dirs(graph.binaries),
        ],
        framework_dirs=xcframework.framework_dirs(graph.binaries),
        simulator=simulator,
    )

    # fingerprint the source list and plugin hashes too
    # a deleted source or rebuilt macro changes output without touching a tracked file
    print_ = fingerprint(
        {
            "swift": toolchain.swift_version,
            "target": target_triple(manifest, simulator=simulator),
            "sdk": sdk_root.name,
            "flags": list(manifest.swift_flags),
            "parse_as_library": parse_as_library,
            "sources": [relative_key(p, layout.project) for p in sources],
            "plugins": sorted(
                (p.module, p.sha256) for p in toolchain.macro_plugins
            ),
            "modules": [t.module for t in graph.targets],
            "binaries": xcframework.identity(graph.binaries),
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
        inputs=(*sources, *(f for b in graph.binaries for f in b.interface_files())),
        outputs=(output,),
    )
