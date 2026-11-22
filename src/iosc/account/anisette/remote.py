import base64
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
from typing import Any
import urllib.error
import urllib.request
import uuid

from iosc.account.anisette.base import AnisetteHeaders, client_time
from iosc.account.anisette.websocket import WebSocket
from iosc.core import AnisetteError, get_reporter, write_atomic
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
# gsa rejects com.apple.dt.Xcode (2026-09), "macOS;" with 503 (2026-11-20), old versions with -22410 (2026-11-21)
DEFAULT_CLIENT_INFO = "<MacBookAir10,1> <Mac OS X;27.3;26C5035r> <com.apple.AuthKit/1 (com.apple.akd/1.0)>"
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


class HTTPStatusError(AnisetteError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


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
        raise HTTPStatusError(exc.code, f"HTTP request failed with status {exc.code} ({err_msg})") from exc
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
    client_info: str = DEFAULT_CLIENT_INFO,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[str, str]:
    dev_id = device_id or generate_device_id()
    lu_uuid = local_user_uuid or generate_local_user_uuid()
    headers = build_provisioning_headers(
        device_id=dev_id, local_user_uuid=lu_uuid, client_info=client_info, user_agent=user_agent
    )

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
            client_info=client_info,
            user_agent=user_agent,
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


V3_CLIENT_INFO_PATH = "/v3/client_info"
V3_HEADERS_PATH = "/v3/get_headers"
V3_PROVISIONING_PATH = "/v3/provisioning_session"
# identities gsa rejects, see DEFAULT_CLIENT_INFO
REJECTED_CLIENT_INFO = ("<macOS;", "dt.Xcode")


@dataclass
class V3State:
    identifier: bytes
    adi_pb: bytes | None = None

    @classmethod
    def fresh(cls) -> "V3State":
        return cls(identifier=secrets.token_bytes(16))

    @property
    def local_user_uuid(self) -> str:
        return hashlib.sha256(self.identifier).hexdigest().upper()

    @property
    def device_id(self) -> str:
        return str(uuid.UUID(bytes=self.identifier)).upper()


class StaleProvisioning(AnisetteError):
    pass


def server_state_dir(url: str) -> Path:
    from iosc.config.paths import state_dir

    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", url.split("://", 1)[-1]).strip("_")
    return state_dir() / "anisette" / slug


def acceptable_client_info(info: str) -> bool:
    return bool(info) and not any(marker in info for marker in REJECTED_CLIENT_INFO)


class RemoteProvider:
    def __init__(
        self,
        url: str,
        timeout: int = 20,
        state_dir: Path | str | None = None,
        client_info: str | None = None,
        user_agent: str | None = None,
        verify_ssl: bool = False,
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise AnisetteError(f"{url!r} is not an http url")
        self.url = url
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.state_path = Path(state_dir or server_state_dir(self.base)) / "state.json"
        self.client_info = client_info
        self.user_agent = user_agent
        self._pinned_identity = client_info is not None
        self.protocol: str | None = None

    def detect(self) -> str:
        if self.protocol is not None:
            return self.protocol
        try:
            info = self._json(self.base + V3_CLIENT_INFO_PATH)
        except HTTPStatusError as exc:
            if exc.status != 404:
                raise
            info = {}
        if not isinstance(info.get("client_info"), str):
            get_reporter().warn(f"{self.base} has no v3 api, using its shared v1 identity")
            self.protocol = "v1"
            return self.protocol

        if not self._pinned_identity:
            served = info["client_info"]
            if acceptable_client_info(served):
                self.client_info = served
                self.user_agent = self.user_agent or str(info.get("user_agent") or DEFAULT_USER_AGENT)
            else:
                get_reporter().warn(f"{self.base} serves a client identity gsa rejects, using the default")
        self.client_info = self.client_info or DEFAULT_CLIENT_INFO
        self.user_agent = self.user_agent or DEFAULT_USER_AGENT
        self.protocol = "v3"
        return self.protocol

    def _json(self, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        content = _http_request(url, headers, body=data, timeout=self.timeout, verify_ssl=self.verify_ssl)
        try:
            payload = json.loads(content)
        except ValueError as exc:
            raise AnisetteError(f"{url} did not answer with json") from exc
        if not isinstance(payload, dict):
            raise AnisetteError(f"{url} answered with {type(payload).__name__}, expected an object")
        return payload

    def load_state(self) -> V3State | None:
        if not self.state_path.is_file():
            return None
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            identifier = base64.b64decode(saved["identifier"])
            adi_pb = base64.b64decode(saved["adi_pb"]) if saved.get("adi_pb") else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if len(identifier) != 16 or adi_pb is None:
            return None
        return V3State(identifier=identifier, adi_pb=adi_pb)

    def save_state(self, state: V3State) -> None:
        data = {
            "server": self.base,
            "identifier": base64.b64encode(state.identifier).decode("ascii"),
            "adi_pb": base64.b64encode(state.adi_pb or b"").decode("ascii"),
        }
        write_atomic(self.state_path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))

    def reset(self) -> None:
        self.state_path.unlink(missing_ok=True)

    def open_session(self) -> WebSocket:
        ws_url = re.sub(r"^http", "ws", self.base) + V3_PROVISIONING_PATH
        return WebSocket.connect(ws_url, timeout=self.timeout, context=_ssl_context(self.verify_ssl))

    def provision(self) -> V3State:
        self.detect()
        state = V3State.fresh()
        identity = {
            "device_id": state.device_id,
            "local_user_uuid": state.local_user_uuid,
            "client_info": self.client_info,
            "user_agent": self.user_agent,
            "verify_ssl": self.verify_ssl,
            "timeout": self.timeout,
        }
        session: ProvisioningSession | None = None
        ws = self.open_session()
        try:
            while True:
                message = ws.recv_json()
                result = message.get("result")
                if result == "GiveIdentifier":
                    ws.send_json({"identifier": base64.b64encode(state.identifier).decode("ascii")})
                elif result == "GiveStartProvisioningData":
                    spim, session = start_provisioning(**identity)
                    ws.send_json({"spim": base64.b64encode(spim).decode("ascii")})
                elif result == "GiveEndProvisioningData":
                    if session is None:
                        raise AnisetteError("anisette server asked to finish before starting")
                    done = end_provisioning(session, cpim=str(message.get("cpim", "")), timeout=self.timeout)
                    ws.send_json(
                        {
                            "ptm": base64.b64encode(done.ptm).decode("ascii"),
                            "tk": base64.b64encode(done.tk).decode("ascii"),
                        }
                    )
                elif result == "ProvisioningSuccess":
                    state.adi_pb = base64.b64decode(message["adi_pb"])
                    break
                else:
                    detail = message.get("message", "")
                    raise AnisetteError(f"anisette server provisioning failed: {result} {detail}".strip())
        finally:
            ws.close()

        self.save_state(state)
        get_reporter().detail(f"provisioned machine {state.device_id} with {self.base}")
        return state

    def machine_headers(self, state: V3State) -> dict[str, str]:
        answer = self._json(
            self.base + V3_HEADERS_PATH,
            body={
                "identifier": base64.b64encode(state.identifier).decode("ascii"),
                "adi_pb": base64.b64encode(state.adi_pb or b"").decode("ascii"),
            },
        )
        result = answer.get("result")
        if result == "GetHeadersError":
            raise StaleProvisioning(f"anisette server rejected the machine: {answer.get('message', '')}")
        if result != "Headers":
            raise AnisetteError(f"anisette server answered get_headers with {result!r}")
        absent = [k for k in ("X-Apple-I-MD", "X-Apple-I-MD-M", "X-Apple-I-MD-RINFO") if not answer.get(k)]
        if absent:
            raise AnisetteError("anisette server did not supply " + ", ".join(absent))
        return {k: str(answer[k]) for k in ("X-Apple-I-MD", "X-Apple-I-MD-M", "X-Apple-I-MD-RINFO")}

    def fetch_v3(self) -> dict[str, str]:
        state = self.load_state() or self.provision()
        try:
            machine = self.machine_headers(state)
        except StaleProvisioning as exc:
            get_reporter().warn(f"{exc}, provisioning again")
            state = self.provision()
            machine = self.machine_headers(state)
        return {
            **machine,
            "X-Apple-I-MD-LU": state.local_user_uuid,
            "X-Mme-Device-Id": state.device_id,
            "X-Mme-Client-Info": str(self.client_info),
            "X-Apple-I-Client-Time": client_time(),
            "X-Apple-I-TimeZone": DEFAULT_TIMEZONE,
            "X-Apple-Locale": DEFAULT_LOCALE,
            "X-Apple-I-SRL-NO": DEFAULT_SERIAL_NO,
        }

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
        if self.detect() == "v3":
            return AnisetteHeaders.from_dict(self.fetch_v3())
        return AnisetteHeaders.from_dict(self.fetch())
