import base64
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import secrets
import ssl
from typing import Any
import urllib.error
import urllib.request
import uuid

from iosc.account.anisette.base import AnisetteHeaders, normalise
from iosc.core import AnisetteError
from iosc.formats.plist import read_plist, write_plist

DEFAULT_LOOKUP_URL = "https://gsa.apple.com/grandslam/GsService2/lookup"

HEADER_USER_AGENT = "User-Agent"
HEADER_CONTENT_TYPE = "Content-Type"
HEADER_CLIENT_INFO = "X-Mme-Client-Info"
HEADER_DEVICE_ID = "X-Mme-Device-Id"
HEADER_LOCAL_USER_UUID = "X-Apple-I-MD-LU"
HEADER_SERIAL_NO = "X-Apple-I-SRL-NO"
HEADER_CLIENT_TIME = "X-Apple-I-Client-Time"
HEADER_TIMEZONE = "X-Apple-I-TimeZone"
HEADER_LOCALE = "X-Apple-Locale"
HEADER_ROUTING_INFO = "X-Apple-I-MD-RINFO"

DEFAULT_USER_AGENT = "akd/1.0 CFNetwork/1404.0.5 Darwin/22.3.0"
DEFAULT_CLIENT_INFO = "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>"
DEFAULT_CONTENT_TYPE = "text/x-xml-plist"
DEFAULT_SERIAL_NO = "0"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_LOCALE = "en_US"

PLIST_KEY_URLS = "urls"
PLIST_KEY_START_PROVISIONING = "midStartProvisioning"
PLIST_KEY_FINISH_PROVISIONING = "midFinishProvisioning"
PLIST_KEY_SYNC_MACHINE = "midSyncMachine"
PLIST_KEY_HEADER = "Header"
PLIST_KEY_REQUEST = "Request"
PLIST_KEY_RESPONSE = "Response"
PLIST_KEY_STATUS = "Status"
PLIST_KEY_ERROR_CODE = "ec"
PLIST_KEY_ERROR_MESSAGE = "em"
PLIST_KEY_ERROR_DETAIL = "ed"
PLIST_KEY_SPIM = "spim"
PLIST_KEY_CPIM = "cpim"
PLIST_KEY_PTM = "ptm"
PLIST_KEY_TK = "tk"
PLIST_KEY_PTXID = "ptxid"


@dataclass(frozen=True)
class ProvisioningSession:
    device_id: str
    local_user_uuid: str
    finish_url: str
    start_url: str = ""
    client_info: str = DEFAULT_CLIENT_INFO
    user_agent: str = DEFAULT_USER_AGENT
    serial_no: str = DEFAULT_SERIAL_NO
    locale: str = DEFAULT_LOCALE
    timezone: str = DEFAULT_TIMEZONE
    ptxid: str = ""
    routing_info: int | None = None
    verify_ssl: bool = False


@dataclass(frozen=True)
class ProvisioningResult:
    ptm: bytes
    tk: bytes
    routing_info: int | None = None
    ptxid: str = ""

    def __iter__(self):
        return iter((self.ptm, self.tk))


def current_client_time(dt: datetime.datetime | None = None) -> str:
    now = dt or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_device_id() -> str:
    return str(uuid.uuid4()).upper()


def generate_local_user_uuid() -> str:
    return secrets.token_hex(32).upper()


def build_provisioning_headers(
    device_id: str,
    local_user_uuid: str,
    client_info: str = DEFAULT_CLIENT_INFO,
    user_agent: str = DEFAULT_USER_AGENT,
    serial_no: str = DEFAULT_SERIAL_NO,
    locale: str = DEFAULT_LOCALE,
    timezone: str = DEFAULT_TIMEZONE,
    now: datetime.datetime | None = None,
) -> dict[str, str]:
    return {
        HEADER_USER_AGENT: user_agent,
        HEADER_CONTENT_TYPE: DEFAULT_CONTENT_TYPE,
        HEADER_CLIENT_INFO: client_info,
        HEADER_DEVICE_ID: device_id,
        HEADER_LOCAL_USER_UUID: local_user_uuid,
        HEADER_SERIAL_NO: serial_no,
        HEADER_CLIENT_TIME: current_client_time(now),
        HEADER_TIMEZONE: timezone,
        HEADER_LOCALE: locale,
    }


def _ssl_context(verify_ssl: bool = False) -> ssl.SSLContext:
    if verify_ssl or os.environ.get("IOSC_VERIFY_SSL") == "1":
        return ssl.create_default_context()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_request(
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: int = 15,
    verify_ssl: bool = False,
) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers)
    ctx = _ssl_context(verify_ssl)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        content = exc.read()
        err_msg = content.decode(errors="replace")[:200]
        raise AnisetteError(f"HTTP request failed with status {exc.code} ({err_msg})") from exc
    except urllib.error.URLError as exc:
        raise AnisetteError(f"Could not reach {url}: {exc.reason}") from exc


def check_apple_response_status(response_dict: dict[str, Any]) -> None:
    status = response_dict.get(PLIST_KEY_STATUS)
    if not isinstance(status, dict):
        return
    code = status.get(PLIST_KEY_ERROR_CODE, 0)
    if code != 0:
        message = status.get(PLIST_KEY_ERROR_MESSAGE, "")
        detail = status.get(PLIST_KEY_ERROR_DETAIL, "")
        raise AnisetteError(f"Apple provisioning server error {code} ({message}) detail={detail}")


def get_provisioning_urls(
    lookup_url: str = DEFAULT_LOOKUP_URL,
    device_id: str | None = None,
    local_user_uuid: str | None = None,
    verify_ssl: bool = False,
    timeout: int = 15,
) -> tuple[str, str]:
    dev_id = device_id or generate_device_id()
    lu_uuid = local_user_uuid or generate_local_user_uuid()
    headers = build_provisioning_headers(device_id=dev_id, local_user_uuid=lu_uuid)

    content = _http_request(lookup_url, headers=headers, timeout=timeout, verify_ssl=verify_ssl)
    parsed = read_plist(content)
    urls = parsed.get(PLIST_KEY_URLS)
    if not isinstance(urls, dict):
        raise AnisetteError(f"Missing '{PLIST_KEY_URLS}' dictionary in lookup response")

    start_url = urls.get(PLIST_KEY_START_PROVISIONING)
    finish_url = urls.get(PLIST_KEY_FINISH_PROVISIONING)
    if not start_url or not finish_url:
        raise AnisetteError(f"Lookup response missing start or finish URLs")

    return str(start_url), str(finish_url)


def start_provisioning(
    lookup_url: str = DEFAULT_LOOKUP_URL,
    start_url: str | None = None,
    finish_url: str | None = None,
    device_id: str | None = None,
    local_user_uuid: str | None = None,
    client_info: str = DEFAULT_CLIENT_INFO,
    user_agent: str = DEFAULT_USER_AGENT,
    serial_no: str = DEFAULT_SERIAL_NO,
    locale: str = DEFAULT_LOCALE,
    timezone: str = DEFAULT_TIMEZONE,
    verify_ssl: bool = False,
    timeout: int = 15,
) -> tuple[bytes, ProvisioningSession]:
    dev_id = device_id or generate_device_id()
    lu_uuid = local_user_uuid or generate_local_user_uuid()

    if not start_url or not finish_url:
        found_start, found_finish = get_provisioning_urls(
            lookup_url=lookup_url,
            device_id=dev_id,
            local_user_uuid=lu_uuid,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        start_url = start_url or found_start
        finish_url = finish_url or found_finish

    headers = build_provisioning_headers(
        device_id=dev_id,
        local_user_uuid=lu_uuid,
        client_info=client_info,
        user_agent=user_agent,
        serial_no=serial_no,
        locale=locale,
        timezone=timezone,
    )

    body = {
        PLIST_KEY_HEADER: {},
        PLIST_KEY_REQUEST: {},
    }
    payload_xml = write_plist(body, binary=False)

    content = _http_request(start_url, headers=headers, body=payload_xml, timeout=timeout, verify_ssl=verify_ssl)
    parsed = read_plist(content)
    response_dict = parsed.get(PLIST_KEY_RESPONSE)
    if not isinstance(response_dict, dict):
        raise AnisetteError(f"Missing '{PLIST_KEY_RESPONSE}' in start provisioning response")

    check_apple_response_status(response_dict)

    spim_b64 = response_dict.get(PLIST_KEY_SPIM)
    if not spim_b64 or not isinstance(spim_b64, str):
        raise AnisetteError(f"Missing '{PLIST_KEY_SPIM}' string in start provisioning response")

    spim_bytes = base64.b64decode(spim_b64)
    ptxid = str(response_dict.get(PLIST_KEY_PTXID, ""))

    session = ProvisioningSession(
        device_id=dev_id,
        local_user_uuid=lu_uuid,
        finish_url=finish_url,
        start_url=start_url,
        client_info=client_info,
        user_agent=user_agent,
        serial_no=serial_no,
        locale=locale,
        timezone=timezone,
        ptxid=ptxid,
        verify_ssl=verify_ssl,
    )
    return spim_bytes, session


def end_provisioning(
    session: ProvisioningSession | dict[str, Any],
    cpim: bytes | str,
    timeout: int = 15,
) -> ProvisioningResult:
    if isinstance(session, ProvisioningSession):
        dev_id = session.device_id
        lu_uuid = session.local_user_uuid
        finish_url = session.finish_url
        client_info = session.client_info
        user_agent = session.user_agent
        serial_no = session.serial_no
        locale = session.locale
        timezone = session.timezone
        verify_ssl = session.verify_ssl
    elif isinstance(session, dict):
        dev_id = session["device_id"]
        lu_uuid = session["local_user_uuid"]
        finish_url = session["finish_url"]
        client_info = session.get("client_info", DEFAULT_CLIENT_INFO)
        user_agent = session.get("user_agent", DEFAULT_USER_AGENT)
        serial_no = session.get("serial_no", DEFAULT_SERIAL_NO)
        locale = session.get("locale", DEFAULT_LOCALE)
        timezone = session.get("timezone", DEFAULT_TIMEZONE)
        verify_ssl = session.get("verify_ssl", False)
    else:
        raise AnisetteError(f"Invalid session type: {type(session).__name__}")

    if isinstance(cpim, bytes):
        cpim_b64 = base64.b64encode(cpim).decode("ascii")
    elif isinstance(cpim, str):
        cpim_b64 = cpim
    else:
        raise AnisetteError(f"Invalid cpim type: {type(cpim).__name__}")

    headers = build_provisioning_headers(
        device_id=dev_id,
        local_user_uuid=lu_uuid,
        client_info=client_info,
        user_agent=user_agent,
        serial_no=serial_no,
        locale=locale,
        timezone=timezone,
    )

    body = {
        PLIST_KEY_HEADER: {},
        PLIST_KEY_REQUEST: {
            PLIST_KEY_CPIM: cpim_b64,
        },
    }
    payload_xml = write_plist(body, binary=False)

    content = _http_request(finish_url, headers=headers, body=payload_xml, timeout=timeout, verify_ssl=verify_ssl)
    parsed = read_plist(content)
    response_dict = parsed.get(PLIST_KEY_RESPONSE)
    if not isinstance(response_dict, dict):
        raise AnisetteError(f"Missing '{PLIST_KEY_RESPONSE}' in finish provisioning response")

    check_apple_response_status(response_dict)

    ptm_b64 = response_dict.get(PLIST_KEY_PTM)
    tk_b64 = response_dict.get(PLIST_KEY_TK)
    if not ptm_b64 or not tk_b64:
        raise AnisetteError("Finish response missing ptm or tk keys")

    ptm_bytes = base64.b64decode(ptm_b64)
    tk_bytes = base64.b64decode(tk_b64)
    ptxid = str(response_dict.get(PLIST_KEY_PTXID, ""))

    routing_val = response_dict.get(HEADER_ROUTING_INFO)
    routing_info: int | None = None
    if routing_val is not None:
        try:
            routing_info = int(routing_val)
        except (ValueError, TypeError):
            pass

    return ProvisioningResult(
        ptm=ptm_bytes,
        tk=tk_bytes,
        routing_info=routing_info,
        ptxid=ptxid,
    )


class RemoteProvider:
    def __init__(self, url: str, timeout: int = 20) -> None:
        if not url.startswith(("http://", "https://")):
            raise AnisetteError(f"{url!r} is not an http url")
        self.url = url
        self.timeout = timeout

    def fetch(self) -> dict[str, Any]:
        req = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        ctx = _ssl_context(verify_ssl=False)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as response:
                body = response.read()
        except urllib.error.URLError as exc:
            raise AnisetteError(f"could not reach {self.url}: {exc}") from exc
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise AnisetteError(f"{self.url} did not answer with json") from exc
        if not isinstance(payload, dict):
            raise AnisetteError(
                f"{self.url} answered with {type(payload).__name__}, expected an object of headers"
            )
        return payload

    def headers(self) -> AnisetteHeaders:
        return AnisetteHeaders.from_dict(self.fetch())
