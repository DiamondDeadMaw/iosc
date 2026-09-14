import base64
from collections.abc import Callable
from dataclasses import dataclass
import datetime
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import ssl
import time
from typing import Any
import urllib.error
import urllib.request

from iosc.account.anisette.base import (
    AnisetteProvider,
    client_provided_data,
    get_provider,
)
from iosc.account.srp import AppleSrpSession, PROTOCOLS
from iosc.config import cache, load_settings
from iosc.core import AuthError, GrandSlamError
from iosc.formats import aes
from iosc.formats.plist import read_plist, write_plist

GSA_URL = "https://gsa.apple.com/grandslam/GsService2"
LOOKUP_URL = GSA_URL + "/lookup"
PHONE_CODE_URL = "https://gsa.apple.com/auth/verify/phone/securitycode"
PHONE_REQUEST_URL = "https://gsa.apple.com/auth/verify/phone"
TRUSTED_DEVICE_URL = "https://gsa.apple.com/auth/verify/trusteddevice"
VALIDATE_CODE_URL = "https://gsa.apple.com/grandslam/GsService2/validate"
AUTH_EXTRAS_URL = "https://gsa.apple.com/auth"
PLIST_TYPE = "text/x-xml-plist"
VERSION = "1.0.1"
TIMEOUT = 30
TOKEN_MARKER = b"XYZ"
RINFO = "17106176"

SECONDARY_ACTION_REQUIRED = 409
SECOND_FACTOR_KEYS = ("trustedDeviceSecondaryAuth", "secondaryAuth")

ERRORS = {
    -20209: "the account is locked",
    -21669: "that verification code is not valid",
    -22406: "the Apple ID or password is wrong",
    -22411: "the session has expired, log in again",
    -36607: "Apple refused the sign in",
}




class TwoFactorRequired(AuthError):
    def __init__(self, factor: "SecondFactor") -> None:
        super().__init__("this Apple ID needs a verification code")
        self.factor = factor


@dataclass(frozen=True)
class ApplicationInfo:
    name: str
    app_id: str
    headers: dict[str, str]


XCODE = ApplicationInfo(
    "Xcode",
    "com.apple.gs.xcode.auth",
    {
        "X-Xcode-Version": "14.2 (14C18)",
        "X-Apple-App-Info": "com.apple.gs.xcode.auth",
    },
)


def _envelope(request: dict[str, Any]) -> bytes:
    return write_plist({"Header": {"Version": VERSION}, "Request": request}, binary=False)


def _describe(status: dict[str, Any]) -> str:
    code = int(status.get("ec", 0))
    return status.get("em") or ERRORS.get(code) or f"error {code}"


def _check(status: dict[str, Any]) -> None:
    code = int(status.get("ec", 0))
    if code:
        raise GrandSlamError(_describe(status), code=code)


def _build_ssl_context() -> ssl.SSLContext:
    if os.environ.get("IOSC_VERIFY_SSL") == "1":
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


_SSL_CONTEXT = _build_ssl_context()


def _http(
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    method: str | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL_CONTEXT) as response:
            return response.status, _maybe_gunzip(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _maybe_gunzip(error.read())
    except urllib.error.URLError as error:
        raise GrandSlamError(f"could not reach {url}: {error.reason}") from error


def _maybe_gunzip(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def _plist(body: bytes, url: str) -> Any:
    try:
        return read_plist(body)
    except Exception as error:
        raise GrandSlamError(f"{url} did not answer with a plist") from error


def _load_apple_plist(data: bytes) -> Any:
    head = data.lstrip()
    if head.startswith(b"<") and not head.startswith((b"<?xml", b"<plist")):
        data = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
            b'<plist version="1.0">' + head + b"</plist>"
        )
    return read_plist(data)


class Client:
    def __init__(
        self,
        provider: AnisetteProvider | None = None,
        application: ApplicationInfo = XCODE,
    ) -> None:
        if provider is None:
            settings = load_settings()
            self.provider = get_provider(settings.anisette_provider)
        else:
            self.provider = provider
        self.application = application
        self._urls: dict[str, str] | None = None

    def _base_headers(self, identity_token: str | None = None) -> dict[str, str]:
        prov_headers = self.provider.headers()
        result = {
            "Content-Type": PLIST_TYPE,
            "Accept": PLIST_TYPE,
            "Accept-Language": "en-us",
            "User-Agent": self.application.name,
            "X-Mme-Client-Info": prov_headers["X-Mme-Client-Info"],
        }
        token = identity_token or getattr(self, "identity_token", None)
        if token:
            result["X-Apple-Identity-Token"] = token
        result.update(self.application.headers)
        return result

    def urls(self) -> dict[str, str]:
        if self._urls is None:
            status, body = _http(LOOKUP_URL, self._base_headers())
            if status != 200:
                raise GrandSlamError(f"the url lookup answered {status}")
            payload = _plist(body, LOOKUP_URL)
            self._urls = {name: str(value) for name, value in payload.get("urls", {}).items()}
            if "gsService" not in self._urls:
                raise GrandSlamError("the url lookup did not name gsService")
        return self._urls

    def _exchange(self, request: dict[str, Any], identity_token: str | None = None) -> dict[str, Any]:
        url = self.urls()["gsService"]
        status, body = _http(url, self._base_headers(identity_token), _envelope(request))
        payload = _plist(body, url)
        if "Response" not in payload:
            raise GrandSlamError(f"the login server answered {status} with no Response")
        return payload["Response"]

    def login(
        self,
        apple_id: str,
        password: str | None = None,
        identity_token: str | None = None,
        *,
        code_callback: Callable[[], str] | None = None,
        password_callback: Callable[[], str] | None = None,
    ) -> "Account":
        if password is None and password_callback is not None:
            password = password_callback()
        if not password:
            raise GrandSlamError("Password is required")

        apple_id = apple_id.lower()
        session = AppleSrpSession()
        cpd = client_provided_data(self.provider)

        first = self._exchange(
            {
                "A2k": session.start(),
                "cpd": cpd,
                "o": "init",
                "ps": list(PROTOCOLS),
                "u": apple_id,
            },
            identity_token=identity_token,
        )
        _check(first.get("Status", {}))
        for field in ("s", "B", "i", "sp", "c"):
            if field not in first:
                raise GrandSlamError(f"the login server left out {field}")

        proof = session.respond(
            apple_id,
            password,
            str(first["sp"]),
            bytes(first["B"]),
            bytes(first["s"]),
            int(first["i"]),
        )
        second = self._exchange(
            {
                "M1": proof,
                "c": first["c"],
                "cpd": client_provided_data(self.provider),
                "o": "complete",
                "u": apple_id,
            },
            identity_token=identity_token,
        )
        status = second.get("Status", {})
        _check(status)

        if "M2" not in second or not session.verify(bytes(second["M2"])):
            raise GrandSlamError(
                "the server did not prove it knows the password, so the connection cannot be trusted"
            )
        if "spd" not in second:
            raise GrandSlamError("the login response carried no session data")

        decrypted = session.decrypt_session_data(bytes(second["spd"]))
        try:
            data = _load_apple_plist(decrypted)
        except Exception as error:
            raise GrandSlamError(
                f"the session data decrypted but did not parse as a plist (head {decrypted[:48]!r})"
            ) from error

        for field in ("adsid", "GsIdmsToken"):
            if field not in data:
                raise GrandSlamError(f"the session data left out {field}")

        current_identity_token = base64.b64encode(
            f"{data['adsid']}:{data['GsIdmsToken']}".encode()
        ).decode()

        if int(status.get("hsc", 0)) == SECONDARY_ACTION_REQUIRED:
            action = str(status.get("au", ""))
            complete_anyway = "sk" in data and "c" in data
            if action in SECOND_FACTOR_KEYS:
                factor = SecondFactor(self, action, str(data["adsid"]), str(data["GsIdmsToken"]))
                if code_callback is not None:
                    factor.send()
                    code = code_callback()
                    factor.submit(code)
                    return self.login(
                        apple_id,
                        password,
                        identity_token=factor.identity_token,
                        code_callback=None,
                        password_callback=None,
                    )
                raise TwoFactorRequired(factor)
            if action != "repair" and not complete_anyway:
                raise GrandSlamError(f"Apple asked for a step this tool does not support: {action}")

        if "sk" not in data or "c" not in data:
            raise GrandSlamError(
                "the login succeeded but Apple withheld the session key, "
                "which usually means the account needs attention at appleid.apple.com"
            )

        app_token_res = self._app_token(
            str(data["adsid"]), str(data["GsIdmsToken"]), bytes(data["sk"]), bytes(data["c"])
        )
        if isinstance(app_token_res, tuple):
            token, token_expiry = app_token_res
        else:
            token = str(app_token_res)
            token_expiry = getattr(app_token_res, "expiry", None)

        if token_expiry is None:
            token_expiry = _extract_expiry(data)

        return Account(
            self,
            apple_id,
            str(data["adsid"]),
            token,
            identity_token=current_identity_token,
            app_token_expiry=token_expiry,
            password=password,
        )

    def _app_token(
        self,
        adsid: str,
        idms_token: str,
        session_key: bytes,
        cookie: bytes,
    ) -> "DecryptedToken":
        app_id = self.application.app_id
        checksum = hmac.new(
            session_key,
            b"apptokens" + adsid.encode() + app_id.encode(),
            hashlib.sha256,
        ).digest()
        response = self._exchange({
            "u": adsid,
            "app": [app_id],
            "c": cookie,
            "t": idms_token,
            "checksum": checksum,
            "cpd": client_provided_data(self.provider),
            "o": "apptokens",
        })
        _check(response.get("Status", {}))
        if "et" not in response:
            raise GrandSlamError("Apple returned no token")
        token_res = _decrypt_app_token(session_key, bytes(response["et"]), app_id)
        if isinstance(token_res, tuple):
            token, expiry = token_res
        else:
            token = str(token_res)
            expiry = getattr(token_res, "expiry", None)
        if expiry is None:
            expiry = _extract_expiry(response)
        return DecryptedToken(token, expiry)


class DecryptedToken(str):
    def __new__(cls, token: str, expiry: int | None = None):
        obj = super().__new__(cls, token)
        obj.expiry = int(expiry) if expiry is not None else None
        return obj

    def __iter__(self):
        return iter((str(self), self.expiry))


def _extract_expiry(source: Any, now: int | None = None) -> int | None:
    if not isinstance(source, dict):
        return None
    keys = (
        "expiry",
        "app_token_expiry",
        "token_expiry",
        "expirationDate",
        "expiration_date",
        "expiryDate",
        "expiry_date",
        "expires",
        "ttl",
        "token_ttl",
        "app_token_ttl",
        "lifetime",
    )
    current_time = int(time.time()) if now is None else int(now)
    for key in keys:
        val = source.get(key)
        if val is None:
            continue
        if isinstance(val, (datetime.datetime, datetime.date)):
            return int(val.timestamp())
        if isinstance(val, (int, float)):
            num = int(val)
            if "ttl" in key or key == "lifetime" or num < 100_000_000:
                return current_time + num
            return num
        if isinstance(val, str):
            val = val.strip()
            if val.isdigit():
                num = int(val)
                if "ttl" in key or key == "lifetime" or num < 100_000_000:
                    return current_time + num
                return num
    return None


def _decrypt_app_token(session_key: bytes, sealed: bytes, app_id: str) -> DecryptedToken:
    if sealed[:3] != TOKEN_MARKER:
        raise GrandSlamError("the token is in a format this tool does not know")
    try:
        plain = aes.decrypt_gcm(session_key, sealed[3:19], sealed[19:], sealed[:3])
    except aes.AesError as error:
        raise GrandSlamError(f"the token did not decrypt: {error}") from error

    payload = _load_apple_plist(plain)
    try:
        app_entry = payload["t"][app_id]
        token = app_entry["token"]
    except (KeyError, TypeError) as error:
        raise GrandSlamError(f"no token for {app_id} in the response") from error
    expiry = _extract_expiry(app_entry) or _extract_expiry(payload)
    return DecryptedToken(token, expiry)


class SecondFactor:
    def __init__(self, client: Client, kind: str, adsid: str, idms_token: str) -> None:
        self.client = client
        self.kind = kind
        self.identity_token = base64.b64encode(f"{adsid}:{idms_token}".encode()).decode()

    @property
    def to_trusted_device(self) -> bool:
        return self.kind == "trustedDeviceSecondaryAuth"

    def _headers(self) -> dict[str, str]:
        prov_headers = self.client.provider.headers()
        result = {
            "Content-Type": PLIST_TYPE,
            "Accept": PLIST_TYPE,
            "Accept-Language": "en-us",
            "User-Agent": self.client.application.name,
            "X-Apple-Identity-Token": self.identity_token,
            "X-Apple-I-MD": prov_headers["X-Apple-I-MD"],
            "X-Apple-I-MD-M": prov_headers["X-Apple-I-MD-M"],
            "X-Apple-I-MD-RINFO": prov_headers.get("X-Apple-I-MD-RINFO", RINFO),
            "X-Apple-I-MD-LU": prov_headers["X-Apple-I-MD-LU"],
            "X-Apple-I-Client-Time": prov_headers["X-Apple-I-Client-Time"],
            "X-Apple-I-TimeZone": prov_headers["X-Apple-I-TimeZone"],
            "X-Apple-Locale": prov_headers["X-Apple-Locale"],
            "Loc": prov_headers["X-Apple-Locale"],
            "X-Mme-Device-Id": prov_headers["X-Mme-Device-Id"],
            "X-Mme-Client-Info": prov_headers["X-Mme-Client-Info"],
        }
        result.update(self.client.application.headers)
        return result

    def send(self) -> bool:
        if self.to_trusted_device:
            status, _ = _http(TRUSTED_DEVICE_URL, self._headers())
            return status == 200
        url = self.client.urls().get(self.kind)
        if not url:
            raise GrandSlamError(f"Apple named no url for {self.kind}")
        status, _ = _http(url, self._headers())
        return status == 200

    def submit(self, code: str) -> bool:
        code = code.strip()
        if not code:
            raise GrandSlamError("no code was entered")
        if self.to_trusted_device:
            return self._submit_to_device(code)
        return self._submit_to_phone(code)

    def _submit_to_device(self, code: str) -> bool:
        headers = self._headers()
        headers["security-code"] = code
        _, body = _http(VALIDATE_CODE_URL, headers)
        payload = _plist(body, VALIDATE_CODE_URL)
        _check(payload)
        return True

    def _json_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return headers

    def trusted_phone_numbers(self) -> list[dict[str, Any]]:
        headers = self._headers()
        headers["Accept"] = "application/json"
        status, body = _http(AUTH_EXTRAS_URL, headers)
        try:
            data = json.loads(body)
        except ValueError as error:
            raise GrandSlamError("the auth extras did not return json") from error
        return data.get("trustedPhoneNumbers", [])

    def send_sms(self, phone_id: int) -> bool:
        body = json.dumps({
            "phoneNumber": {"id": phone_id},
            "mode": "sms",
            "securityCode": None,
        }).encode()
        status, _ = _http(PHONE_REQUEST_URL, self._json_headers(), body, method="PUT")
        return status in (200, 201, 204)

    def submit_sms(self, code: str, phone_id: int) -> bool:
        body = json.dumps({
            "phoneNumber": {"id": phone_id},
            "mode": "sms",
            "securityCode": {"code": code},
        }).encode()
        status, response = _http(PHONE_CODE_URL, self._json_headers(), body, method="POST")
        if status != 200:
            raise GrandSlamError(
                f"the code was not accepted: {response.decode(errors='replace')[:200]}"
            )
        return True

    def _submit_to_phone(self, code: str) -> bool:
        numbers = self.trusted_phone_numbers()
        if not numbers:
            raise GrandSlamError("Apple listed no trusted phone number")
        return self.submit_sms(code, numbers[0]["id"])


class Account:
    def __init__(
        self,
        client: Client | None,
        apple_id: str,
        adsid: str,
        token: str,
        identity_token: str | None = None,
        app_token_expiry: int | None = None,
        token_expiry: int | None = None,
        password: str | None = None,
        login_callable: Any | None = None,
    ) -> None:
        self.client = client
        self.apple_id = apple_id
        self.adsid = adsid
        self.token = token
        self.identity_token = identity_token
        expiry = app_token_expiry if app_token_expiry is not None else token_expiry
        self.app_token_expiry = int(expiry) if expiry is not None else None
        self.token_expiry = self.app_token_expiry
        self.password = password
        self.login_callable = login_callable

    def headers(self) -> dict[str, str]:
        client_provider = getattr(self.client, "provider", None) if self.client else None
        headers = client_provider.headers() if client_provider else None
        client_app = getattr(self.client, "application", None) if self.client else None
        app_name = client_app.name if client_app else "Xcode"
        result = {
            "Content-Type": PLIST_TYPE,
            "Accept": PLIST_TYPE,
            "Accept-Language": "en-us",
            "User-Agent": app_name,
            "X-Apple-I-Identity-Id": self.adsid,
            "X-Apple-GS-Token": self.token,
            "X-Apple-I-MD": headers["X-Apple-I-MD"] if headers else "",
            "X-Apple-I-MD-M": headers["X-Apple-I-MD-M"] if headers else "",
            "X-Apple-I-MD-LU": headers["X-Apple-I-MD-LU"] if headers else "",
            "X-Apple-I-MD-RINFO": headers.get("X-Apple-I-MD-RINFO", RINFO) if headers else RINFO,
            "X-Apple-I-Client-Time": headers.get("X-Apple-I-Client-Time", "") if headers else "",
            "X-Apple-I-TimeZone": headers.get("X-Apple-I-TimeZone", "") if headers else "",
            "X-Apple-Locale": headers.get("X-Apple-Locale", "") if headers else "",
            "X-Mme-Device-Id": headers.get("X-Mme-Device-Id", "") if headers else "",
            "X-Mme-Client-Info": headers.get("X-Mme-Client-Info", "") if headers else "",
        }
        if client_app:
            result.update(client_app.headers)
        return result

    def post(self, url: str, request: dict[str, Any]) -> Any:
        status, body = _http(url, self.headers(), write_plist(request, binary=False))
        payload = _plist(body, url)
        if status != 200 and not isinstance(payload, dict):
            raise GrandSlamError(f"{url} answered {status}")
        return payload

    def summary(self) -> str:
        return f"{self.apple_id} ({self.adsid})"


def login_with_cache(
    client: Client,
    apple_id: str,
    password: str | None = None,
    cache_dir: str | Path | None = None,
    login_callable: Any | None = None,
    *,
    code_callback: Callable[[], str] | None = None,
    password_callback: Callable[[], str] | None = None,
) -> Account:
    tokens = cache.get_auth_tokens(apple_id, root_dir=cache_dir)
    if cache.is_app_token_valid(tokens) and tokens:
        return Account(
            client,
            apple_id.lower(),
            str(tokens.get("adsid", "")),
            str(tokens["app_token"]),
            identity_token=tokens.get("identity_token"),
            app_token_expiry=tokens.get("app_token_expiry"),
            password=password,
            login_callable=login_callable,
        )

    identity_token = None
    if cache.is_identity_token_valid(tokens) and tokens:
        identity_token = tokens.get("identity_token")

    login_func = login_callable or client.login
    if login_callable is not None:
        account = login_func(apple_id, password, identity_token=identity_token)
    else:
        account = client.login(
            apple_id,
            password,
            identity_token=identity_token,
            code_callback=code_callback,
            password_callback=password_callback,
        )

    if password is not None and getattr(account, "password", None) is None:
        account.password = password
    if login_callable is not None and getattr(account, "login_callable", None) is None:
        account.login_callable = login_callable

    payload: dict[str, Any] = {
        "app_token": account.token,
        "adsid": account.adsid,
        "identity_token": account.identity_token or identity_token,
    }
    if getattr(account, "app_token_expiry", None) is not None:
        payload["app_token_expiry"] = account.app_token_expiry

    cache.set_auth_tokens(apple_id, payload, root_dir=cache_dir)
    return account
