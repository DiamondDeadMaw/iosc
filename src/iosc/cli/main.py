import argparse
import sys
from typing import Sequence

from iosc.cli.commands import (
    auth as auth_cmd,
    build as build_cmd,
    clean as clean_cmd,
    crash as crash_cmd,
    deps as deps_cmd,
    dev as dev_cmd,
    devices as devices_cmd,
    init as init_cmd,
    install as install_cmd,
    launch as launch_cmd,
    log as log_cmd,
    package as package_cmd,
    run as run_cmd,
    sdk as sdk_cmd,
    sign as sign_cmd,
    toolchain as toolchain_cmd,
)
from iosc.config.manifest import REQUIREMENT_KINDS
from iosc.core import logging
from iosc.core.errors import IoscError, UsageError


class IoscArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def add_signing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--certificate", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--udid", default=None)
    parser.add_argument("--apple-id", dest="apple_id", default=None)
    parser.add_argument("--team-id", dest="team_id", default=None)
    parser.add_argument(
        "--revoke-and-reissue", dest="revoke_and_reissue", action="store_true"
    )
    parser.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    # suppressed defaults. a subparser copy wont clobber a pre-subcommand value
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS)
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    parser = IoscArgumentParser(prog="iosc", parents=[common])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", parents=[common])
    init_parser.add_argument("--name", default=None)
    init_parser.add_argument("--bundle-id", dest="bundle_id", default=None)
    init_parser.set_defaults(handler=init_cmd.run)

    sdk_parser = subparsers.add_parser("sdk", parents=[common])
    sdk_subparsers = sdk_parser.add_subparsers(dest="subcommand", required=True)

    sdk_extract = sdk_subparsers.add_parser("extract", parents=[common])
    sdk_extract.add_argument("xip")
    sdk_extract.add_argument("--no-resume", dest="no_resume", action="store_true")
    sdk_extract.set_defaults(handler=sdk_cmd.run)

    sdk_status = sdk_subparsers.add_parser("status", parents=[common])
    sdk_status.set_defaults(handler=sdk_cmd.run)

    sdk_verify = sdk_subparsers.add_parser("verify", parents=[common])
    sdk_verify.set_defaults(handler=sdk_cmd.run)

    auth_parser = subparsers.add_parser("auth", parents=[common])
    auth_subparsers = auth_parser.add_subparsers(dest="subcommand", required=True)

    auth_login = auth_subparsers.add_parser("login", parents=[common])
    auth_login.add_argument("apple_id")
    auth_login.add_argument("--anisette", default="local")
    auth_login.set_defaults(handler=auth_cmd.run)

    auth_status = auth_subparsers.add_parser("status", parents=[common])
    auth_status.add_argument("apple_id")
    auth_status.set_defaults(handler=auth_cmd.run)

    auth_apk_extract = auth_subparsers.add_parser("apk-extract", parents=[common])
    auth_apk_extract.add_argument("apkm", nargs="?", default=None)
    auth_apk_extract.set_defaults(handler=auth_cmd.run)

    toolchain_parser = subparsers.add_parser("toolchain", parents=[common])
    toolchain_subparsers = toolchain_parser.add_subparsers(dest="subcommand", required=True)

    toolchain_fetch = toolchain_subparsers.add_parser("fetch", parents=[common])
    toolchain_fetch.add_argument("--force", action="store_true")
    toolchain_fetch.set_defaults(handler=toolchain_cmd.run)

    toolchain_status = toolchain_subparsers.add_parser("status", parents=[common])
    toolchain_status.set_defaults(handler=toolchain_cmd.run)

    build_parser_cmd = subparsers.add_parser("build", parents=[common])
    build_parser_cmd.add_argument("--force", action="store_true")
    build_parser_cmd.set_defaults(handler=build_cmd.run)

    sign_parser = subparsers.add_parser("sign", parents=[common])
    add_signing_arguments(sign_parser)
    sign_parser.set_defaults(handler=sign_cmd.run)

    package_parser = subparsers.add_parser("package", parents=[common])
    package_parser.set_defaults(handler=package_cmd.run)

    deps_parser = subparsers.add_parser("deps", parents=[common])
    deps_subparsers = deps_parser.add_subparsers(dest="subcommand", required=True)

    deps_show = deps_subparsers.add_parser("show", parents=[common])
    deps_show.set_defaults(handler=deps_cmd.run)

    deps_resolve = deps_subparsers.add_parser("resolve", parents=[common])
    deps_resolve.set_defaults(handler=deps_cmd.run)

    deps_update = deps_subparsers.add_parser("update", parents=[common])
    deps_update.add_argument("packages", nargs="*")
    deps_update.set_defaults(handler=deps_cmd.run)

    deps_add = deps_subparsers.add_parser("add", parents=[common])
    deps_add.add_argument("source")
    deps_add.add_argument("--name", default=None)
    deps_add.add_argument("--product", action="append", default=[])
    for kind in REQUIREMENT_KINDS:
        deps_add.add_argument(f"--{kind}", dest=kind, default=None)
    deps_add.set_defaults(handler=deps_cmd.run)

    devices_parser = subparsers.add_parser("devices", parents=[common])
    devices_parser.set_defaults(handler=devices_cmd.run)

    install_parser = subparsers.add_parser("install", parents=[common])
    install_parser.add_argument("--udid", default=None)
    install_parser.set_defaults(handler=install_cmd.run)

    launch_parser = subparsers.add_parser("launch", parents=[common])
    launch_parser.add_argument("--udid", default=None)
    launch_parser.add_argument("--env", action="append", default=[])
    launch_parser.add_argument(
        "--no-kill-existing", dest="no_kill_existing", action="store_true"
    )
    launch_parser.add_argument("--suspended", action="store_true")
    # everything after -- goes to the app untouched, e.g. iosc launch -- --flag value
    launch_parser.add_argument("app_args", nargs=argparse.REMAINDER, default=[])
    launch_parser.set_defaults(handler=launch_cmd.run)

    log_parser = subparsers.add_parser("log", parents=[common])
    log_parser.add_argument("--udid", default=None)
    log_parser.add_argument("--contains", default=None)
    log_parser.add_argument("--os-log", dest="os_log", action="store_true")
    log_parser.add_argument("--pid", type=int, default=None)
    log_parser.add_argument("--process-name", dest="process_name", default=None)
    log_parser.add_argument("--match", action="append", default=[])
    log_parser.set_defaults(handler=log_cmd.run)

    crash_parser = subparsers.add_parser("crash", parents=[common])
    crash_subparsers = crash_parser.add_subparsers(dest="subcommand", required=True)

    crash_list = crash_subparsers.add_parser("list", parents=[common])
    crash_list.add_argument("--udid", default=None)
    crash_list.add_argument("--remote-path", dest="remote_path", default="/")
    crash_list.add_argument("--depth", type=int, default=1)
    crash_list.set_defaults(handler=crash_cmd.run)

    crash_pull = crash_subparsers.add_parser("pull", parents=[common])
    crash_pull.add_argument("out")
    crash_pull.add_argument("--udid", default=None)
    crash_pull.add_argument("--remote-path", dest="remote_path", default="/")
    crash_pull.add_argument("--match", default=None)
    crash_pull.add_argument("--erase", action="store_true")
    crash_pull.set_defaults(handler=crash_cmd.run)

    crash_symbolicate = crash_subparsers.add_parser("symbolicate", parents=[common])
    crash_symbolicate.add_argument("report")
    crash_symbolicate.add_argument("--binary", action="append", default=[])
    crash_symbolicate.add_argument("--dsym", action="append", default=[])
    crash_symbolicate.add_argument("--thread", type=int, default=None)
    crash_symbolicate.set_defaults(handler=crash_cmd.run)

    run_parser = subparsers.add_parser("run", parents=[common])
    add_signing_arguments(run_parser)
    run_parser.add_argument("--no-log", dest="no_log", action="store_true")
    run_parser.add_argument("--launch", action="store_true")
    run_parser.set_defaults(handler=run_cmd.run)

    clean_parser = subparsers.add_parser("clean", parents=[common])
    clean_parser.set_defaults(handler=clean_cmd.run)

    dev_parser = subparsers.add_parser("dev", parents=[common])
    dev_parser.add_argument("probe")
    dev_parser.set_defaults(handler=dev_cmd.run)

    return parser


GLOBAL_DEFAULTS = {"project": None, "verbose": False, "json": False}


def apply_global_defaults(args: argparse.Namespace) -> argparse.Namespace:
    for name, value in GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    return args


# handlers read args.json and args.verbose. defaults fill them in later
def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return apply_global_defaults(build_parser().parse_args(argv))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse(argv)
        logging.configure(verbose=args.verbose, json_mode=args.json)
        if not hasattr(args, "handler") or args.handler is None:
            raise UsageError("Command required")
        args.handler(args)
        return 0
    except UsageError as err:
        logging.error(err.message)
        return 2
    except IoscError as err:
        logging.error(err.message)
        return 1
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
