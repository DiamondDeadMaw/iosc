from dataclasses import replace
from pathlib import Path

from iosc.build.compile import STAGE_NAME as COMPILE_STAGE
from iosc.build.compile import object_path
from iosc.build.graph import BuildLayout, Stage, fingerprint
from iosc.build.packages import PackageGraph
from iosc.build.packages import object_path as package_object_path
from iosc.config import paths
from iosc.config.manifest import Manifest
from iosc.core.progress import Heartbeat
from iosc.toolchain.discovery import Toolchain
from iosc.toolchain.linker import LinkSpec, link

STAGE_NAME = "link"
# xcode app default for embedded swift back deployment libs
APP_RPATHS = ("@executable_path/Frameworks",)


def executable_path(layout: BuildLayout, product_name: str) -> Path:
    return layout.obj / product_name


def link_stage(
    manifest: Manifest,
    layout: BuildLayout,
    toolchain: Toolchain,
    sdk_root: Path,
    product_name: str,
    packages: PackageGraph | None = None,
) -> Stage:
    graph = packages if packages is not None else PackageGraph()
    objects = [object_path(layout, product_name)] + [
        package_object_path(layout, t.module) for t in graph.targets
    ]
    output = executable_path(layout, product_name)
    frameworks = list(manifest.frameworks)
    frameworks += [f for f in graph.frameworks if f not in frameworks]
    spec = LinkSpec(
        objects=objects,
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        frameworks=frameworks,
        clang_rt=paths.clang_rt_ios(),
        rpaths=list(APP_RPATHS),
        extra_flags=list(manifest.linker_flags),
    )
    spec = replace(
        spec,
        libraries=spec.libraries + [lib for lib in graph.libraries if lib not in spec.libraries],
    )

    print_ = fingerprint(
        {
            "linker": str(toolchain.linker),
            "deployment_target": manifest.deployment_target,
            "sdk": sdk_root.name,
            "frameworks": list(spec.frameworks),
            "libraries": list(spec.libraries),
            "clang_rt": spec.clang_rt.name if spec.clang_rt else None,
            "rpaths": list(spec.rpaths),
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
        inputs=tuple(objects),
        outputs=(output,),
    )
