from iosc.config import paths
from iosc.core import logging
from iosc.core.errors import UsageError
from iosc.device import Device, find_device, tunnel_running
from iosc.sdk import layout as sdk_layout
from iosc.toolchain import discovery


def probe_sdk() -> dict[str, object]:
    found = sdk_layout.from_root()
    return {
        "root": str(found.root),
        "version": found.platform_version,
        "frameworks": len(list(found.frameworks_dir.glob("*.framework"))),
    }


def probe_toolchain() -> dict[str, object]:
    chain = discovery.detect()
    return {
        "swiftc": str(chain.swiftc),
        "linker": str(chain.linker),
        "version": chain.swift_version,
    }


def probe_device() -> dict[str, object]:
    return Device(find_device()).info_dict()


# surface a stray RemoteXPC tunnel daemon
# it can outlive the command that wanted it (ARCHITECTURE.md section 13)
def probe_tunnel() -> dict[str, object]:
    return {"running": tunnel_running()}


def probe_paths() -> dict[str, object]:
    assets = (
        paths.sdk_root(),
        paths.ld64_lld(),
        paths.apple_certs_dir(),
        paths.apk_dir(),
    )
    found = {a.name: {"path": str(a.path), "present": paths.exists(a)} for a in assets}
    # bundled into the executable, not external
    # a frozen build that missed them fails here, not mid sdk extract
    lzfse = paths.vendor_lzfse_dir()
    for name in ("lzfse.exe", "lzvn_raw.exe"):
        found[name] = {
            "path": str(lzfse / name),
            "present": (lzfse / name).is_file(),
        }
    return found


PROBES = {
    "sdk": probe_sdk,
    "toolchain": probe_toolchain,
    "device": probe_device,
    "paths": probe_paths,
    "tunnel": probe_tunnel,
}


def run(args) -> None:
    if args.probe not in PROBES:
        raise UsageError(
            f"unknown probe '{args.probe}', try one of {', '.join(sorted(PROBES))}"
        )
    result = PROBES[args.probe]()

    if args.json:
        logging.result(result)
        return
    for key, value in result.items():
        logging.info(f"{key}: {value}")
