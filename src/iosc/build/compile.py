from pathlib import Path

from iosc.build.graph import BuildLayout, Stage, fingerprint, relative_key
from iosc.config.manifest import Manifest, resolve_sources
from iosc.core.progress import Heartbeat
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain.swift import CompileSpec, compile_objects

STAGE_NAME = "swift"


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
) -> Stage:
    sources = resolve_sources(manifest, layout.project)
    output = object_path(layout, product_name)
    spec = CompileSpec(
        sources=sources,
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        extra_flags=list(manifest.swift_flags),
    )

    # fingerprint the source list and plugin hashes too
    # a deleted source or rebuilt macro changes output without touching a tracked file
    print_ = fingerprint(
        {
            "swift": toolchain.swift_version,
            "target": target_triple(manifest),
            "sdk": sdk_root.name,
            "flags": list(manifest.swift_flags),
            "sources": [relative_key(p, layout.project) for p in sources],
            "plugins": sorted(
                (p.module, p.sha256) for p in toolchain.macro_plugins
            ),
        }
    )

    def run() -> None:
        with Heartbeat(f"compiling {len(sources)} swift sources"):
            compile_objects(toolchain, spec)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        inputs=tuple(sources),
        outputs=(output,),
    )
