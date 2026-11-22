from dataclasses import asdict, replace

from iosc.account import session
from iosc.account.anisette import (
    DEFAULT_ANISETTE_SERVER,
    extract_adi_libraries,
    is_valid_source,
    normalise_source,
    source_origin,
)
from iosc.cli.commands.sign import ask_code, ask_password, checked_anisette
from iosc.config import load_settings, paths, save_settings
from iosc.core import logging
from iosc.core.errors import UsageError


def run_login(args) -> None:
    account = session.login(
        args.apple_id,
        provider_name=checked_anisette(args.anisette),
        cache_dir=str(paths.state_dir()),
        password_callback=ask_password(args.apple_id),
        code_callback=ask_code,
    )
    if args.json:
        logging.result({"apple_id": args.apple_id, "adsid": account.adsid})
        return
    logging.success(f"signed in as {args.apple_id}")


def run_status(args) -> None:
    source, origin = source_origin(checked_anisette(args.anisette))
    state = session.status(args.apple_id, cache_dir=str(paths.state_dir())) if args.apple_id else None
    if args.json:
        payload = {"anisette": source, "anisette_origin": origin}
        if state is not None:
            payload.update(asdict(state))
        logging.result(payload)
        return
    logging.info(f"anisette source {source} ({origin})")
    if state is None:
        return
    if not state.logged_in:
        logging.info(f"not signed in as {args.apple_id}, run 'iosc auth login'")
        return
    trusted = "trusted device saved" if state.identity_token_valid else "two factor needed next time"
    logging.success(f"signed in as {args.apple_id}, {trusted}")


def run_set_anisette(args) -> None:
    value = args.source.strip()
    if value.lower() == "default":
        stored = None
    elif is_valid_source(value):
        stored = normalise_source(value)
    else:
        raise UsageError(f"set-anisette takes local, an http(s) url or default, not {value!r}")
    save_settings(replace(load_settings(), anisette_provider=stored))
    if args.json:
        logging.result({"anisette": stored or DEFAULT_ANISETTE_SERVER, "stored": stored})
        return
    logging.success(f"anisette source set to {stored or DEFAULT_ANISETTE_SERVER + ' (default)'}")


def run_apk_extract(args) -> None:
    extracted = extract_adi_libraries(apk_path=args.apkm, abi=args.abi)
    if args.json:
        logging.result({"extracted": [str(p) for p in extracted]})
        return
    if not extracted:
        logging.warn("no ADI libraries matched inside that archive, is it Apple Music 6.5.2?")
        return
    logging.success(f"extracted {len(extracted)} ADI libraries to {extracted[0].parent}")


HANDLERS = {
    "login": run_login,
    "status": run_status,
    "set-anisette": run_set_anisette,
    "apk-extract": run_apk_extract,
}


def run(args) -> None:
    HANDLERS[args.subcommand](args)
