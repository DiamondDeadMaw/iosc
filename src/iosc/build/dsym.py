from pathlib import Path

from iosc.build.graph import BuildLayout, Stage, fingerprint
from iosc.config import paths
from iosc.core import ExternalToolError, get_reporter, process, rmtree_force
from iosc.core.errors import DebugError
from iosc.core.progress import Heartbeat

STAGE_NAME = "dsym"


def dsym_path(layout: BuildLayout, product_name: str) -> Path:
    return layout.root / f"{product_name}.dSYM"


# the stage cache hashes declared outputs as plain files, a .dSYM is a
# bundle directory, so point at the one file dsymutil actually writes inside it
def dwarf_path(layout: BuildLayout, product_name: str) -> Path:
    return dsym_path(layout, product_name) / "Contents" / "Resources" / "DWARF" / product_name


def run_dsymutil(dsymutil: Path, executable: Path, output: Path) -> None:
    argv = [str(dsymutil), str(executable), "-o", str(output)]
    try:
        process.run(argv)
    except ExternalToolError as err:
        msg = err.stderr if err.stderr else str(err)
        raise DebugError(f"dsymutil failed: {msg}") from err


# dsymutil is optional, a build without it just keeps symbol+offset crashes
# available is decided at plan time so an unfetched tool never becomes a
# declared output the cache expects but run() never produces
def dsym_stage(
    layout: BuildLayout,
    executable: Path,
    product_name: str,
    depends_on: tuple[str, ...] = (),
) -> Stage:
    output = dsym_path(layout, product_name)
    dsymutil = paths.dsymutil_tool()
    available = paths.exists(dsymutil)

    print_ = fingerprint({"dsymutil": str(dsymutil.path), "available": available})

    def run() -> None:
        if not available:
            get_reporter().detail(f"{dsymutil.hint} skipping dSYM")
            return
        rmtree_force(output)
        with Heartbeat(f"generating dSYM for {product_name}"):
            run_dsymutil(dsymutil.path, executable, output)

    return Stage(
        name=STAGE_NAME,
        run=run,
        fingerprint=print_,
        depends_on=depends_on,
        inputs=(executable,),
        outputs=(dwarf_path(layout, product_name),) if available else (),
    )
