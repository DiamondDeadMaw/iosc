from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from iosc.config.paths import cache_dir as default_cache_dir
from iosc.core import write_atomic

CERT_EXPIRY_MARGIN = 7 * 86400
PROFILE_EXPIRY_MARGIN = 86400
DEFAULT_APP_TOKEN_TTL = 86400
DEFAULT_IDENTITY_TOKEN_TTL = 2592000


@dataclass(frozen=True)
class CachedCertificate:
    pem: bytes
    key: bytes
    expiry: int | None = None

    def __iter__(self):
        return iter((self.pem, self.key))


@dataclass(frozen=True)
class CachedProfile:
    data: bytes
    expiry: int
    bundle_id: str
    device_id: str


def get_cache_dir(root_dir: Path | str | None = None) -> Path:
    if root_dir is not None:
        return Path(root_dir).resolve()
    env = os.environ.get("IOSC_CACHE_DIR")
    if env:
        return Path(env).resolve()
    return default_cache_dir()


def auth_dir(apple_id: str, root_dir: Path | str | None = None) -> Path:
    hashed = hashlib.sha256(apple_id.lower().encode("utf-8")).hexdigest()
    return get_cache_dir(root_dir) / "auth" / hashed


def get_auth_tokens(apple_id: str, root_dir: Path | str | None = None) -> dict[str, Any] | None:
    path = auth_dir(apple_id, root_dir) / "tokens.json"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    if not content:
        return None
    return json.loads(content)


def set_auth_tokens(apple_id: str, tokens: dict[str, Any], root_dir: Path | str | None = None) -> None:
    path = auth_dir(apple_id, root_dir) / "tokens.json"
    payload = dict(tokens)
    now = int(time.time())
    if "app_token" in payload and payload.get("app_token_expiry") is None:
        payload["app_token_expiry"] = now + DEFAULT_APP_TOKEN_TTL
    if "identity_token" in payload and payload.get("identity_token_expiry") is None:
        payload["identity_token_expiry"] = now + DEFAULT_IDENTITY_TOKEN_TTL
    if payload.get("app_token_expiry") is not None:
        payload["app_token_expiry"] = int(payload["app_token_expiry"])
    if payload.get("identity_token_expiry") is not None:
        payload["identity_token_expiry"] = int(payload["identity_token_expiry"])
    write_atomic(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def invalidate_app_token(apple_id: str, root_dir: Path | str | None = None) -> None:
    tokens = get_auth_tokens(apple_id, root_dir=root_dir)
    if not tokens:
        return
    tokens.pop("app_token", None)
    tokens.pop("app_token_expiry", None)
    set_auth_tokens(apple_id, tokens, root_dir=root_dir)


def is_app_token_valid(tokens: dict[str, Any] | None, now: int | None = None) -> bool:
    if not tokens or not tokens.get("app_token"):
        return False
    current_time = int(time.time()) if now is None else int(now)
    return int(tokens.get("app_token_expiry", 0)) > current_time


def is_identity_token_valid(tokens: dict[str, Any] | None, now: int | None = None) -> bool:
    if not tokens or not tokens.get("identity_token"):
        return False
    current_time = int(time.time()) if now is None else int(now)
    return int(tokens.get("identity_token_expiry", 0)) > current_time


def cert_dir(team_id: str, root_dir: Path | str | None = None) -> Path:
    return get_cache_dir(root_dir) / "certs" / team_id


def get_certificate(team_id: str, root_dir: Path | str | None = None) -> CachedCertificate | None:
    folder = cert_dir(team_id, root_dir)
    pem_path = folder / "dev.pem"
    key_path = folder / "dev.key"
    if not pem_path.is_file() or not key_path.is_file():
        return None
    pem_bytes = pem_path.read_bytes()
    key_bytes = key_path.read_bytes()
    expiry = None
    sidecar = folder / "cert.json"
    if sidecar.is_file():
        info = json.loads(sidecar.read_text(encoding="utf-8"))
        expiry = info.get("expiry")
    return CachedCertificate(pem=pem_bytes, key=key_bytes, expiry=expiry)


def set_certificate(
    team_id: str,
    cert_data: bytes | str,
    key_data: bytes | str,
    root_dir: Path | str | None = None,
    expiry: int | None = None,
) -> None:
    folder = cert_dir(team_id, root_dir)
    pem_bytes = cert_data.encode("utf-8") if isinstance(cert_data, str) else cert_data
    key_bytes = key_data.encode("utf-8") if isinstance(key_data, str) else key_data
    write_atomic(folder / "dev.pem", pem_bytes)
    write_atomic(folder / "dev.key", key_bytes)
    if expiry is not None:
        sidecar = folder / "cert.json"
        write_atomic(
            sidecar,
            (json.dumps({"expiry": int(expiry), "team_id": team_id}, indent=2) + "\n").encode("utf-8"),
        )


def get_private_key(team_id: str, root_dir: Path | str | None = None) -> bytes | None:
    folder = cert_dir(team_id, root_dir)
    key_path = folder / "dev.key"
    if not key_path.is_file():
        return None
    return key_path.read_bytes()


def set_private_key(team_id: str, key_data: bytes | str, root_dir: Path | str | None = None) -> None:
    folder = cert_dir(team_id, root_dir)
    key_bytes = key_data.encode("utf-8") if isinstance(key_data, str) else key_data
    write_atomic(folder / "dev.key", key_bytes)


def is_certificate_valid(
    cert_or_expiry: Any,
    now: int | None = None,
    margin: int = CERT_EXPIRY_MARGIN,
) -> bool:
    if cert_or_expiry is None:
        return False
    current_time = int(time.time()) if now is None else int(now)
    if isinstance(cert_or_expiry, int):
        return (cert_or_expiry - current_time) > margin
    if hasattr(cert_or_expiry, "expiry") and cert_or_expiry.expiry is not None:
        return (int(cert_or_expiry.expiry) - current_time) > margin
    if hasattr(cert_or_expiry, "not_after"):
        return (int(cert_or_expiry.not_after.timestamp()) - current_time) > margin
    return False


def profile_paths(bundle_id: str, device_id: str, root_dir: Path | str | None = None) -> tuple[Path, Path]:
    base_dir = get_cache_dir(root_dir) / "profiles"
    name = f"{bundle_id}__{device_id}.mobileprovision"
    return base_dir / name, base_dir / f"{name}.json"


def get_profile(bundle_id: str, device_id: str, root_dir: Path | str | None = None) -> CachedProfile | None:
    path, sidecar_path = profile_paths(bundle_id, device_id, root_dir)
    if not path.is_file():
        return None
    data = path.read_bytes()
    expiry = get_profile_expiry(bundle_id, device_id, root_dir) or 0
    return CachedProfile(data=data, expiry=expiry, bundle_id=bundle_id, device_id=device_id)


def get_profile_expiry(bundle_id: str, device_id: str, root_dir: Path | str | None = None) -> int | None:
    _, sidecar_path = profile_paths(bundle_id, device_id, root_dir)
    if not sidecar_path.is_file():
        return None
    content = sidecar_path.read_text(encoding="utf-8")
    if not content:
        return None
    payload = json.loads(content)
    return int(payload.get("expiry", 0))


def set_profile(
    bundle_id: str,
    device_id: str,
    profile_data: Any,
    root_dir: Path | str | None = None,
    expiry: int | None = None,
) -> None:
    path, sidecar_path = profile_paths(bundle_id, device_id, root_dir)
    data = profile_data if isinstance(profile_data, bytes) else getattr(profile_data, "data", bytes(profile_data))
    eff_expiry = int(expiry) if expiry is not None else int(time.time()) + 7 * 86400
    write_atomic(path, data)
    sidecar_data = {
        "expiry": eff_expiry,
        "bundle_id": bundle_id,
        "device_id": device_id,
    }
    write_atomic(sidecar_path, (json.dumps(sidecar_data, indent=2) + "\n").encode("utf-8"))


def is_profile_valid(
    profile_or_expiry: Any,
    sidecar_expiry: int | None = None,
    now: int | None = None,
    margin: int = PROFILE_EXPIRY_MARGIN,
) -> bool:
    if profile_or_expiry is None:
        return False
    current_time = int(time.time()) if now is None else int(now)
    if sidecar_expiry is not None:
        expiry_ts = int(sidecar_expiry)
    elif isinstance(profile_or_expiry, int):
        expiry_ts = profile_or_expiry
    elif hasattr(profile_or_expiry, "expiry") and profile_or_expiry.expiry:
        expiry_ts = int(profile_or_expiry.expiry)
    elif hasattr(profile_or_expiry, "expiration_date") and profile_or_expiry.expiration_date:
        expiry_ts = int(profile_or_expiry.expiration_date.timestamp())
    else:
        return False
    return (expiry_ts - current_time) > margin


def devices_path(root_dir: Path | str | None = None) -> Path:
    return get_cache_dir(root_dir) / "devices.json"


def get_devices(root_dir: Path | str | None = None) -> list[dict[str, Any]] | None:
    path = devices_path(root_dir)
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    if not content:
        return None
    return json.loads(content)


def set_devices(devices: list[Any], root_dir: Path | str | None = None) -> None:
    path = devices_path(root_dir)
    serializable = []
    for d in devices:
        if hasattr(d, "device_id"):
            serializable.append({
                "deviceId": d.device_id,
                "name": d.name,
                "deviceNumber": d.udid,
            })
        elif isinstance(d, dict):
            serializable.append(d)
    write_atomic(path, (json.dumps(serializable, indent=2) + "\n").encode("utf-8"))


def find_device(udid: str, root_dir: Path | str | None = None) -> dict[str, Any] | None:
    devices = get_devices(root_dir)
    if not devices:
        return None
    target = udid.lower()
    for d in devices:
        num = d.get("deviceNumber") or d.get("udid", "")
        if str(num).lower() == target:
            return d
    return None
