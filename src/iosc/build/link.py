from dataclasses import replace
from pathlib import Path

from iosc.build import xcframework
from iosc.build.compile import STAGE_NAME as COMPILE_STAGE
from iosc.build.compile import object_path
from iosc.build.graph import BuildLayout, Stage, fingerprint
from iosc.build.packages import PackageGraph
from iosc.build.packages import object_paths
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
    simulator: bool = False,
) -> Stage:
    graph = packages if packages is not None else PackageGraph()
    objects = [object_path(layout, product_name)]
    for target in graph.targets:
        objects += object_paths(target, layout)
    objects += xcframework.archives(graph.binaries)
    output = executable_path(layout, product_name)
    frameworks = list(manifest.frameworks)
    frameworks += [f for f in graph.frameworks if f not in frameworks]
    frameworks += xcframework.linked_frameworks(graph.binaries)
    spec = LinkSpec(
        objects=objects,
        output=output,
        sdk_root=sdk_root,
        deployment_target=manifest.deployment_target,
        frameworks=frameworks,
        framework_dirs=xcframework.framework_dirs(graph.binaries),
        clang_rt=paths.clang_rt_ios(),
        rpaths=list(APP_RPATHS),
        extra_flags=list(manifest.linker_flags),
        simulator=simulator,
    )
    spec = replace(
        spec,
        libraries=spec.libraries + [lib for lib in graph.libraries if lib not in spec.libraries],
    )

    print_ = fingerprint(
        {
            "linker": str(toolchain.linker),
            "deployment_target": manifest.deployment_target,
            "simulator": simulator,
            "sdk": sdk_root.name,
            "frameworks": list(spec.frameworks),
            "libraries": list(spec.libraries),
            "binaries": xcframework.identity(graph.binaries),
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
        inputs=(*objects, *(b.link_file for b in graph.binaries if b.kind == xcframework.FRAMEWORK)),
        outputs=(output,),
    )
