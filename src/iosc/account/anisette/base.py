from dataclasses import dataclass
import datetime
import json
from pathlib import Path
from typing import Any, Protocol

from iosc.core import AnisetteError

MACHINE_HEADERS = (
    "X-Apple-I-MD",
    "X-Apple-I-MD-M",
    "X-Apple-I-MD-RINFO",
    "X-Apple-I-MD-LU",
    "X-Mme-Device-Id",
    "X-Mme-Client-Info",
)

ALIASES = {
    "x-mme-client-info": "X-Mme-Client-Info",
    "x-apple-i-md": "X-Apple-I-MD",
    "x-apple-i-md-m": "X-Apple-I-MD-M",
    "x-apple-i-md-rinfo": "X-Apple-I-MD-RINFO",
    "x-apple-i-md-lu": "X-Apple-I-MD-LU",
    "x-mme-device-id": "X-Mme-Device-Id",
    "x-apple-i-client-time": "X-Apple-I-Client-Time",
    "x-apple-i-timezone": "X-Apple-I-TimeZone",
    "x-apple-locale": "X-Apple-Locale",
    "x-apple-i-srl-no": "X-Apple-I-SRL-NO",
}


def client_time(now: datetime.datetime | None = None) -> str:
    dt = now or datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(headers: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for name, value in headers.items():
        key = ALIASES.get(name.lower(), name)
        output[key] = str(value)
    return output


def missing(headers: dict[str, str]) -> list[str]:
    return [name for name in MACHINE_HEADERS if not headers.get(name)]


@dataclass(frozen=True)
class AnisetteHeaders:
    otp: str
    mid: str
    routing_info: str
    local_user_uuid: str
    device_id: str
    client_info: str
    client_time: str
    timezone: str = "UTC"
    locale: str = "en_US"
    serial_no: str = "0"

    def as_dict(self) -> dict[str, str]:
        return {
            "X-Apple-I-MD": self.otp,
            "X-Apple-I-MD-M": self.mid,
            "X-Apple-I-MD-RINFO": self.routing_info,
            "X-Apple-I-MD-LU": self.local_user_uuid,
            "X-Mme-Device-Id": self.device_id,
            "X-Mme-Client-Info": self.client_info,
            "X-Apple-I-Client-Time": self.client_time,
            "X-Apple-I-TimeZone": self.timezone,
            "X-Apple-Locale": self.locale,
            "X-Apple-I-SRL-NO": self.serial_no,
        }

    def __getitem__(self, key: str) -> str:
        d = self.as_dict()
        lookup = ALIASES.get(key.lower(), key)
        if lookup in d:
            return d[lookup]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        d = self.as_dict()
        lookup = ALIASES.get(key.lower(), key)
        return d.get(lookup, default)

    def __contains__(self, key: str) -> bool:
        return key in self.as_dict()

    def keys(self):
        return self.as_dict().keys()

    def items(self):
        return self.as_dict().items()

    def values(self):
        return self.as_dict().values()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnisetteHeaders":
        norm = normalise(data)
        absent = missing(norm)
        if absent:
            raise AnisetteError("the anisette provider did not supply " + ", ".join(absent))

        c_time = norm.get("X-Apple-I-Client-Time") or client_time()
        tz = norm.get("X-Apple-I-TimeZone", "UTC")
        loc = norm.get("X-Apple-Locale", "en_US")
        srl = norm.get("X-Apple-I-SRL-NO", "0")

        return cls(
            otp=norm["X-Apple-I-MD"],
            mid=norm["X-Apple-I-MD-M"],
            routing_info=norm["X-Apple-I-MD-RINFO"],
            local_user_uuid=norm["X-Apple-I-MD-LU"],
            device_id=norm["X-Mme-Device-Id"],
            client_info=norm["X-Mme-Client-Info"],
            client_time=c_time,
            timezone=tz,
            locale=loc,
            serial_no=srl,
        )


class AnisetteProvider(Protocol):
    def headers(self) -> AnisetteHeaders:
        ...


class StaticProvider:
    def __init__(self, headers: dict[str, Any] | AnisetteHeaders) -> None:
        if isinstance(headers, AnisetteHeaders):
            self._headers = headers
        else:
            self._headers = AnisetteHeaders.from_dict(dict(headers))

    def fetch(self) -> dict[str, str]:
        return self._headers.as_dict()

    def headers(self) -> AnisetteHeaders:
        return self._headers


class FileProvider:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> dict[str, Any]:
        try:
            content = self.path.read_text(encoding="utf-8")
            payload = json.loads(content)
        except OSError as exc:
            raise AnisetteError(f"cannot read {self.path}: {exc}") from exc
        except ValueError as exc:
            raise AnisetteError(f"{self.path} is not valid json") from exc

        if not isinstance(payload, dict):
            raise AnisetteError(f"{self.path} must hold an object of headers")
        return payload

    def headers(self) -> AnisetteHeaders:
        raw = self.fetch()
        return AnisetteHeaders.from_dict(raw)


def client_provided_data(provider: AnisetteProvider, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    hdrs = provider.headers()
    data = {
        "X-Apple-I-Client-Time": hdrs["X-Apple-I-Client-Time"],
        "X-Apple-I-MD": hdrs["X-Apple-I-MD"],
        "X-Apple-I-MD-LU": hdrs["X-Apple-I-MD-LU"],
        "X-Apple-I-MD-M": hdrs["X-Apple-I-MD-M"],
        "X-Apple-I-MD-RINFO": hdrs["X-Apple-I-MD-RINFO"],
        "X-Apple-I-SRL-NO": hdrs["X-Apple-I-SRL-NO"],
        "X-Apple-I-TimeZone": hdrs["X-Apple-I-TimeZone"],
        "X-Apple-Locale": hdrs["X-Apple-Locale"],
        "X-Mme-Device-Id": hdrs["X-Mme-Device-Id"],
        "bootstrap": True,
        "icscrec": True,
        "loc": hdrs["X-Apple-Locale"],
        "pbe": False,
        "prkgen": True,
        "svct": "iCloud",
    }
    if extra:
        data.update(extra)
    return data


def warnings() -> list[str]:
    return [
        "The free Apple ID route is unofficial and unsupported by Apple. It "
        "depends on anisette data produced by Apple's code, which this "
        "tool does not generate.",
        "Apps signed with a free Apple ID stop launching after about a week "
        "and must be re-signed and reinstalled.",
    ]


def get_provider(source: str | None = None, **kwargs: Any) -> AnisetteProvider:
    if source is None:
        source = "local"
    source = str(source).strip()

    if source.startswith(("http://", "https://")):
        from iosc.account.anisette.remote import RemoteProvider

        return RemoteProvider(url=source, **kwargs)
    if source.lower() == "local":
        from iosc.account.anisette.local_adi import LocalADIProvider

        return LocalADIProvider(**kwargs)
    if source.lower() == "remote":
        from iosc.account.anisette.remote import RemoteProvider

        url = kwargs.pop("url", None)
        if not url:
            raise AnisetteError("RemoteProvider requires a 'url' parameter")
        return RemoteProvider(url=url, **kwargs)
    if source.lower() == "file":
        path = kwargs.pop("path", None)
        if not path:
            raise AnisetteError("FileProvider requires a 'path' parameter")
        return FileProvider(path)
    if source.lower() == "static":
        headers = kwargs.pop("headers", None)
        if headers is None:
            raise AnisetteError("StaticProvider requires a 'headers' parameter")
        return StaticProvider(headers)
    if source.endswith(".json") or Path(source).is_file():
        return FileProvider(source)

    raise AnisetteError(f"unknown anisette provider source: {source!r}")
