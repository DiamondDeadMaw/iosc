from dataclasses import asdict

from iosc.account import session
from iosc.account.anisette import extract_adi_libraries
from iosc.cli.commands.sign import ask_code, ask_password
from iosc.config import paths
from iosc.core import logging


def run_login(args) -> None:
    account = session.login(
        args.apple_id,
        provider_name=args.anisette,
        cache_dir=str(paths.state_dir()),
        password_callback=ask_password(args.apple_id),
        code_callback=ask_code,
    )
    if args.json:
        logging.result({"apple_id": args.apple_id, "adsid": account.adsid})
        return
    logging.success(f"signed in as {args.apple_id}")


def run_status(args) -> None:
    state = session.status(args.apple_id, cache_dir=str(paths.state_dir()))
    if args.json:
        logging.result(asdict(state))
        return
    if not state.logged_in:
        logging.info(f"not signed in as {args.apple_id}, run 'iosc auth login'")
        return
    trusted = "trusted device saved" if state.identity_token_valid else "two factor needed next time"
    logging.success(f"signed in as {args.apple_id}, {trusted}")


def run_apk_extract(args) -> None:
    extracted = extract_adi_libraries(apk_path=args.apkm)
    if args.json:
        logging.result({"extracted": [str(p) for p in extracted]})
        return
    if not extracted:
        logging.warn("no ADI libraries matched inside that archive, is it Apple Music 6.5.2?")
        return
    logging.success(f"extracted {len(extracted)} ADI libraries to {extracted[0].parent}")


HANDLERS = {"login": run_login, "status": run_status, "apk-extract": run_apk_extract}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
