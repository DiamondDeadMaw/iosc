from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iosc.account import anisette
from iosc.account.grandslam import (
    Account,
    Client,
    TwoFactorRequired,
)
from iosc.config import cache
from iosc.core import logging
from iosc.core.errors import AuthError


@dataclass(frozen=True)
class SessionStatus:
    apple_id: str
    logged_in: bool
    app_token_valid: bool
    identity_token_valid: bool
    adsid: str | None = None
    app_token_expiry: int | None = None
    identity_token_expiry: int | None = None


def login(
    apple_id: str,
    password: str | None = None,
    provider_name: str = "local",
    cache_dir: str | Path | None = None,
    no_cache: bool = False,
    code_callback: Callable[[], str] | None = None,
    password_callback: Callable[[], str] | None = None,
    provider_kwargs: dict[str, Any] | None = None,
) -> Account:
    reporter = logging.get_reporter()
    kwargs = dict(provider_kwargs or {})

    try:
        provider = anisette.get_provider(provider_name, **kwargs)
    except Exception as exc:
        raise AuthError(f"Could not set up {provider_name} anisette provider: {exc}") from exc

    reporter.detail("Fetching anisette headers")
    client = Client(provider=provider)

    cached_tokens = cache.get_auth_tokens(apple_id, root_dir=cache_dir) if not no_cache else None
    if cached_tokens and cache.is_app_token_valid(cached_tokens):
        reporter.info(f"Using cached GrandSlam app token for {apple_id}")
        # replayed token still needs fresh device headers every request
        return Account(
            client,
            apple_id.lower(),
            str(cached_tokens.get("adsid", "")),
            str(cached_tokens["app_token"]),
            identity_token=cached_tokens.get("identity_token"),
            app_token_expiry=cached_tokens.get("app_token_expiry"),
            login_callable=client.login,
        )

    identity_token = None
    if cached_tokens and cache.is_identity_token_valid(cached_tokens):
        identity_token = cached_tokens.get("identity_token")
        reporter.detail("Using cached trusted device token to skip two factor authentication")

    if password is None:
        if password_callback is not None:
            password = password_callback()
        else:
            raise AuthError("Password is required for Apple ID login")

    try:
        account = client.login(apple_id, password, identity_token=identity_token)
    except TwoFactorRequired as need:
        factor = need.factor
        reporter.info("Apple requested a verification code")
        sent = factor.send()
        if not sent:
            raise AuthError("Failed to request verification code from Apple")
        if code_callback is None:
            raise AuthError("Two factor authentication code required but no code callback provided")
        code = code_callback().strip()
        accepted = factor.submit(code)
        if not accepted:
            raise AuthError("Verification code was not accepted by Apple")
        account = client.login(apple_id, password, identity_token=factor.identity_token)

    if not no_cache:
        payload: dict[str, Any] = {
            "app_token": account.token,
            "adsid": account.adsid,
            "identity_token": account.identity_token or identity_token,
        }
        if account.app_token_expiry is not None:
            payload["app_token_expiry"] = account.app_token_expiry
        cache.set_auth_tokens(apple_id, payload, root_dir=cache_dir)

    reporter.success(f"Successfully logged in as {apple_id}")
    return account


def status(apple_id: str, cache_dir: str | Path | None = None) -> SessionStatus:
    reporter = logging.get_reporter()
    tokens = cache.get_auth_tokens(apple_id, root_dir=cache_dir)
    app_valid = cache.is_app_token_valid(tokens)
    id_valid = cache.is_identity_token_valid(tokens)
    adsid = str(tokens.get("adsid")) if tokens and "adsid" in tokens else None
    app_exp = int(tokens["app_token_expiry"]) if tokens and "app_token_expiry" in tokens else None
    id_exp = int(tokens["identity_token_expiry"]) if tokens and "identity_token_expiry" in tokens else None

    stat = SessionStatus(
        apple_id=apple_id,
        logged_in=app_valid,
        app_token_valid=app_valid,
        identity_token_valid=id_valid,
        adsid=adsid,
        app_token_expiry=app_exp,
        identity_token_expiry=id_exp,
    )
    if app_valid:
        reporter.info(f"Session active for {apple_id}")
    else:
        reporter.info(f"No active session for {apple_id}")
    return stat


def invalidate_session(apple_id: str, cache_dir: str | Path | None = None) -> None:
    reporter = logging.get_reporter()
    cache.invalidate_app_token(apple_id, root_dir=cache_dir)
    reporter.info(f"Invalidated session for {apple_id}")
