from pathlib import Path

from iosc.build.compile import STAGE_NAME as COMPILE_STAGE
from iosc.build.compile import object_path
from iosc.build.graph import BuildLayout, Stage, fingerprint
from iosc.config import paths
from iosc.config.manifest import Manifest
from iosc.core.progress import Heartbeat
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain.linker import LinkSpec, link

STAGE_NAME = "link"


def executable_path(layout: BuildLayout, product_name: str) -> Path:
    return layout.obj / product_name


def link_stage(
    manifest: Manifest,
    layout: BuildLayout,
    toolchain: Toolchain,
    sdk_root: Path,
    product_name: str,
) -> Stage:
    obj = object_path(layout, product_name)
    output = executable_path(layout, product_name)
    spec = LinkSpec(
        objects=[obj],
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        frameworks=list(manifest.frameworks),
        clang_rt=paths.clang_rt_ios(),
        extra_flags=list(manifest.linker_flags),
    )

    print_ = fingerprint(
        {
            "linker": str(toolchain.linker),
            "deployment_target": manifest.deployment_target,
            "sdk": sdk_root.name,
            "frameworks": list(manifest.frameworks),
            "libraries": list(spec.libraries),
            "clang_rt": spec.clang_rt.name if spec.clang_rt else None,
            "linker_flags": list(manifest.linker_flags),
        }
    )

    def run() -> None:
        with Heartbeat(f"linking {product_name}"):
            link(toolchain, spec)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        depends_on=(COMPILE_STAGE,),
        inputs=(obj,),
        outputs=(output,),
    )
