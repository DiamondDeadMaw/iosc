from iosc.account.anisette.apk import REQUIRED_APK
from iosc.config import paths
from iosc.config.settings import Settings
from iosc.config.settings import load as load_settings
from iosc.core import logging
from iosc.core.color import BOLD, CYAN, DIM, GREEN, RED, YELLOW, paint
from iosc.core.errors import IoscError, MissingExternalAsset
from iosc.device import list_devices
from iosc.toolchain import discovery, fetch, msvc


def run_fetch(args) -> None:
    tools = fetch.fetch_tools(force=args.force)

    if args.json:
        logging.result({k: str(v) for k, v in tools.items()})
        return
    logging.success(f"linker at {tools['linker']}")
    logging.success(f"dsymutil at {tools['dsymutil']}")
    logging.success(f"llvm-symbolizer at {tools['llvm_symbolizer']}")


def _probe(label: str, resolve) -> dict[str, object]:
    try:
        return {"name": label, "ok": True, "detail": str(resolve())}
    except IoscError as err:
        return {"name": label, "ok": False, "detail": err.message}


# mux reachability, not whether a phone is attached
# caught here instead of at install time
def _describe_usbmux() -> str:
    attached = list_devices()
    if not attached:
        return "running, no device attached"
    return "running, " + ", ".join(f"{d.serial} {d.connection_type}" for d in attached)


# only needed for 'iosc auth login', not for signing with a supplied
# certificate/profile, so it does not gate overall readiness
def _describe_anisette() -> str:
    if not paths.exists(paths.adi_bridge()):
        raise MissingExternalAsset(paths.adi_bridge().hint)
    present = {p.name for p in paths.adi_lib_dir().path.glob("*.so")}
    missing = [name for name in paths.adi_libraries() if name not in present]
    if missing:
        raise MissingExternalAsset(
            f"place an {REQUIRED_APK} .apkm in external/apk, then run 'iosc auth apk-extract'"
        )
    return "ready, native bridge and ADI libraries present"


def run_status(args) -> None:
    settings: Settings = load_settings()
    checks = [
        _probe("swiftc", lambda: discovery.find_swiftc(settings)),
        _probe("linker", lambda: discovery.find_linker()),
    ]

    # xcode xip is the raw input the sdk is extracted from, not itself
    # a tracked asset. only worth a row when neither it nor the sdk
    # it produces has shown up yet, otherwise it is redundant noise
    if not paths.exists(paths.sdk_root()) and not paths.exists(paths.sdk_extract_root()):
        checks.append(
            {
                "name": "xcode xip",
                "ok": False,
                "detail": "required for sdk, download it yourself from Apple",
            }
        )

    checks.append(_probe("sdk", lambda: paths.require(paths.sdk_root())))
    checks.append(_probe("apple certificates", lambda: paths.require(paths.apple_certs_dir())))
    checks.append(_probe("device service", _describe_usbmux))
    optional = [
        _probe("anisette (free apple id)", _describe_anisette),
        _probe("dsymutil (file:line in crashes)", lambda: paths.require(paths.dsymutil_tool())),
        _probe("llvm-symbolizer (file:line in crashes)", lambda: paths.require(paths.llvm_symbolizer_tool())),
        _probe("msvc build tools (swift packages)", msvc.find_vcvarsall),
        _probe("git (swift packages from a url)", discovery.find_git),
    ]

    external = str(paths.external_root())
    external_source = paths.external_root_source()

    if args.json:
        logging.result(
            {
                "external_root": external,
                "external_root_source": external_source,
                "checks": checks,
                "optional": optional,
            }
        )
        return

    logging.info(f"{paint('external root', BOLD, CYAN)} {external} ({external_source})")
    logging.info("")

    name_width = max(len(c["name"]) for c in checks + optional) + 2
    rule = paint("-" * (name_width + 8), DIM)

    def print_section(title: str, section_checks: list[dict[str, object]]) -> None:
        logging.info(paint(title, BOLD, CYAN))
        for check in section_checks:
            mark = paint(" ok ", BOLD, GREEN) if check["ok"] else paint(" no ", BOLD, RED)
            name = f"{check['name']:<{name_width}}"
            tail = check["detail"] if check["ok"] else ""
            logging.info(f"  {mark} {name}{tail}")
            if not check["ok"]:
                logging.info(paint(f"       {' ' * name_width}{check['detail']}", DIM))
        logging.info("")

    print_section("required", checks)
    print_section("optional", optional)
    logging.info(rule)

    missing = [c["name"] for c in checks if not c["ok"]]
    if missing:
        logging.warn(paint(f"not ready, missing {', '.join(missing)}", BOLD, YELLOW))
        return
    version = discovery.detect(settings).swift_version
    logging.success(paint(f"ready, {version}", BOLD, GREEN))


HANDLERS = {"fetch": run_fetch, "status": run_status}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
