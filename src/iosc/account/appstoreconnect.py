import base64
import datetime
import hashlib
import hmac
import json
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from iosc.account.types import ProvisioningProfile, IssuedCertificate
from iosc.core.errors import AscError
from iosc.formats import asn1, pki
from iosc.formats.mobileprovision import Profile

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
Q = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

BASE_URL = "https://api.appstoreconnect.apple.com/v1"


def point_add(p1: tuple[int, int] | None, p2: tuple[int, int] | None) -> tuple[int, int] | None:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 != y2:
        return None
    if x1 == x2:
        if y1 == 0:
            return None
        m = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        m = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (m * m - x1 - x2) % P
    y3 = (m * (x1 - x3) - y1) % P
    return x3, y3


def point_mul(k: int, pt: tuple[int, int]) -> tuple[int, int] | None:
    res = None
    curr: tuple[int, int] | None = pt
    while k:
        if k & 1:
            res = point_add(res, curr)
        curr = point_add(curr, curr)
        k >>= 1
    return res


def rfc6979_k(msg_hash: bytes, priv_scalar: int) -> int:
    z1 = int.from_bytes(msg_hash, "big")
    z2 = z1 % Q
    x_octets = priv_scalar.to_bytes(32, "big")
    h_octets = z2.to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x_octets + h_octets, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x_octets + h_octets, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        t = b""
        while len(t) < 32:
            v = hmac.new(k, v, hashlib.sha256).digest()
            t += v
        k_val = int.from_bytes(t[:32], "big")
        if 1 <= k_val < Q:
            pt = point_mul(k_val, (GX, GY))
            assert pt is not None
            r = pt[0] % Q
            if r != 0:
                s = (pow(k_val, -1, Q) * (z1 + r * priv_scalar)) % Q
                if s != 0:
                    return k_val
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def ecdsa_sign(data: bytes, priv_scalar: int) -> tuple[int, int]:
    msg_hash = hashlib.sha256(data).digest()
    z = int.from_bytes(msg_hash, "big")
    nonce = rfc6979_k(msg_hash, priv_scalar)
    pt = point_mul(nonce, (GX, GY))
    assert pt is not None
    r = pt[0] % Q
    s = (pow(nonce, -1, Q) * (z + r * priv_scalar)) % Q
    return r, s


def parse_p8(p8_source: Any) -> int:
    if isinstance(p8_source, (str, bytes)) and ("-----BEGIN" in str(p8_source)):
        pem_text = p8_source
    else:
        with open(p8_source, "r", encoding="utf-8") as f:
            pem_text = f.read()
    der = pki.decode_pem(pem_text, "PRIVATE KEY")
    node = asn1.parse_one(der)
    children = node.children
    if len(children) >= 3 and children[2].tag == asn1.OCTET_STRING:
        inner = asn1.parse_one(children[2].value)
        return int.from_bytes(inner.child(1).value, "big")
    if len(children) >= 2 and children[1].tag == asn1.OCTET_STRING:
        return int.from_bytes(children[1].value, "big")
    raise AscError("unsupported private key format")


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(
    key_id: str,
    issuer_id: str,
    priv_scalar: int,
    now: int | None = None,
    lifetime: int = 1200,
) -> tuple[str, int]:
    now_ts = int(time.time()) if now is None else int(now)
    exp = now_ts + min(lifetime, 1200)
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {
        "iss": issuer_id,
        "iat": now_ts,
        "exp": exp,
        "aud": "appstoreconnect-v1",
    }
    h_b64 = base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    r, s = ecdsa_sign(signing_input, priv_scalar)
    sig_bytes = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = base64url_encode(sig_bytes)
    return f"{h_b64}.{p_b64}.{sig_b64}", exp


def _extract_id(ref: Any) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict) and "id" in ref:
        return str(ref["id"])
    raise AscError(f"invalid reference {ref!r}")


def _get_attribute(obj: dict[str, Any], name: str) -> Any:
    attributes = obj.get("attributes", {}) if isinstance(obj, dict) else {}
    if name in attributes:
        return attributes[name]
    raise AscError(f"attribute {name} not found")


# unwrap apple's {"data": {...}} envelope
def _resource(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _collection(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        items = payload.get("data", [])
        return list(items) if isinstance(items, list) else []
    return list(payload) if isinstance(payload, list) else []


def _default_transport(req: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class AppStoreConnect:
    def __init__(
        self,
        key_id: str,
        issuer_id: str,
        p8_path_or_pem: Any,
        transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.key_id = key_id
        self.issuer_id = issuer_id
        self.private_key = parse_p8(p8_path_or_pem)
        self.transport = transport or _default_transport
        self._cached_token: str | None = None
        self._token_exp = 0

    def token(self) -> str:
        now = int(time.time())
        if self._cached_token and now < self._token_exp - 60:
            return self._cached_token
        self._cached_token, self._token_exp = make_jwt(
            self.key_id, self.issuer_id, self.private_key, now=now
        )
        return self._cached_token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{BASE_URL}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "Accept": "application/json",
        }
        body_bytes = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            if isinstance(body, (dict, list)):
                body_bytes = json.dumps(body).encode("utf-8")
            else:
                body_bytes = body
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        res = self.transport(req)
        if isinstance(res, tuple):
            if len(res) == 3:
                status, _, resp_data = res
            elif len(res) == 2:
                status, resp_data = res
            else:
                raise AscError(f"unexpected transport response length {len(res)}")
        else:
            status = 200
            resp_data = res

        if isinstance(resp_data, str):
            resp_data = resp_data.encode("utf-8")

        if status >= 400:
            err_msg = ""
            errors = []
            if resp_data:
                try:
                    parsed = json.loads(resp_data.decode("utf-8"))
                    errors = parsed.get("errors", [])
                    details = []
                    for e in errors:
                        code = e.get("code")
                        detail = e.get("detail")
                        if code and detail:
                            details.append(f"{code} {detail}")
                        elif detail:
                            details.append(detail)
                        elif code:
                            details.append(code)
                    if details:
                        err_msg = "; ".join(details)
                except Exception:
                    pass
            if not err_msg:
                err_msg = f"HTTP {status}"
            raise AscError(err_msg, status=status, errors=errors)

        if not resp_data:
            return None
        if isinstance(resp_data, (dict, list)):
            data = resp_data
        else:
            data = json.loads(resp_data.decode("utf-8"))
        return data

    def list_certificates(self, certificate_type: str | None = None) -> list[Any]:
        path = "/certificates"
        if certificate_type:
            path += f"?filter[certificateType]={certificate_type}"
        return _collection(self.request("GET", path))

    def create_certificate(
        self,
        csr_pem: str,
        certificate_type: str = "IOS_DEVELOPMENT",
    ) -> Any:
        body = {
            "data": {
                "type": "certificates",
                "attributes": {
                    "certificateType": certificate_type,
                    "csrContent": csr_pem,
                },
            }
        }
        return _resource(self.request("POST", "/certificates", body=body))

    def list_devices(self, platform: str | None = None) -> list[Any]:
        path = "/devices"
        if platform:
            path += f"?filter[platform]={platform}"
        return _collection(self.request("GET", path))

    def register_device(
        self,
        name: str,
        udid: str,
        platform: str = "IOS",
    ) -> Any:
        body = {
            "data": {
                "type": "devices",
                "attributes": {
                    "name": name,
                    "platform": platform,
                    "udid": udid,
                },
            }
        }
        try:
            return _resource(self.request("POST", "/devices", body=body))
        except AscError as exc:
            if exc.status == 409:
                for dev in self.list_devices():
                    dev_udid = dev.get("attributes", {}).get("udid", "")
                    if dev_udid.lower() == udid.lower():
                        return dev
            raise

    def list_bundle_ids(self) -> list[Any]:
        return _collection(self.request("GET", "/bundleIds"))

    def create_bundle_id(
        self,
        identifier: str,
        name: str,
        platform: str = "IOS",
    ) -> Any:
        body = {
            "data": {
                "type": "bundleIds",
                "attributes": {
                    "identifier": identifier,
                    "name": name,
                    "platform": platform,
                },
            }
        }
        try:
            return _resource(self.request("POST", "/bundleIds", body=body))
        except AscError as exc:
            if exc.status == 409:
                for item in self.list_bundle_ids():
                    if item.get("attributes", {}).get("identifier") == identifier:
                        return item
            raise

    def list_profiles(self, profile_type: str | None = None) -> list[Any]:
        path = "/profiles"
        if profile_type:
            path += f"?filter[profileType]={profile_type}"
        return _collection(self.request("GET", path))

    def create_profile(
        self,
        name: str,
        bundle_id_ref: Any,
        certificate_refs: Any,
        device_refs: Any,
        profile_type: str = "IOS_APP_DEVELOPMENT",
    ) -> Any:
        bid = _extract_id(bundle_id_ref)
        if not isinstance(certificate_refs, (list, tuple)):
            certificate_refs = [certificate_refs]
        cert_ids = [_extract_id(c) for c in certificate_refs]
        if not isinstance(device_refs, (list, tuple)):
            device_refs = [device_refs]
        dev_ids = [_extract_id(d) for d in device_refs]
        body = {
            "data": {
                "type": "profiles",
                "attributes": {
                    "name": name,
                    "profileType": profile_type,
                },
                "relationships": {
                    "bundleId": {
                        "data": {"type": "bundleIds", "id": bid}
                    },
                    "certificates": {
                        "data": [{"type": "certificates", "id": cid} for cid in cert_ids]
                    },
                    "devices": {
                        "data": [{"type": "devices", "id": did} for did in dev_ids]
                    },
                },
            }
        }
        return _resource(self.request("POST", "/profiles", body=body))

    def delete_profile(self, profile_id: str) -> Any:
        return self.request("DELETE", f"/profiles/{profile_id}")

    def obtain_identity_and_profile(
        self,
        bundle_id: str,
        device_udid: str,
        device_name: str,
        certificate_type: str = "IOS_DEVELOPMENT",
        profile_type: str = "IOS_APP_DEVELOPMENT",
        key: pki.PrivateKey | None = None,
    ) -> tuple[IssuedCertificate, ProvisioningProfile]:
        key = key or pki.PrivateKey.generate(2048)
        dev = self.register_device(device_name, device_udid)
        dev_id = _extract_id(dev)
        bid = self.create_bundle_id(bundle_id, bundle_id)
        bid_id = _extract_id(bid)

        chosen_cert = None
        chosen_cert_id = None
        for item in self.list_certificates(certificate_type=certificate_type):
            content_b64 = item.get("attributes", {}).get("certificateContent")
            if not content_b64:
                continue
            parsed_cert = pki.Certificate(base64.b64decode(content_b64))
            if parsed_cert.is_expired() or not parsed_cert.matches(key):
                continue
            chosen_cert = parsed_cert
            chosen_cert_id = item.get("id")
            break

        if chosen_cert is None:
            subject = [
                ("CN", f"iPhone Developer: {device_name}"),
                ("OU", "iosc"),
                ("O", "iosc"),
                ("C", "US"),
            ]
            csr_pem = pki.certificate_request_pem(key, subject)
            cert_res = self.create_certificate(csr_pem, certificate_type=certificate_type)
            chosen_cert_id = _extract_id(cert_res)
            content_b64 = _get_attribute(cert_res, "certificateContent")
            chosen_cert = pki.Certificate(base64.b64decode(content_b64))

        prof_name = f"iosc {bundle_id}"
        prof_res = self.create_profile(
            prof_name,
            bid_id,
            [chosen_cert_id],
            [dev_id],
            profile_type=profile_type,
        )
        prof_b64 = _get_attribute(prof_res, "profileContent")
        prof_bytes = base64.b64decode(prof_b64)
        profile = Profile.from_bytes(prof_bytes)
        problems = profile.problems(
            bundle_id,
            device_udid,
            chosen_cert,
            datetime.datetime.now(),
        )
        if problems:
            raise AscError(
                "Apple issued a profile that does not fit this build: " + "; ".join(problems)
            )
        identity = IssuedCertificate(
            certificate=chosen_cert.der,
            private_key=key.to_pem().encode("ascii"),
            team_id=chosen_cert.team_id or "iosc",
        )
        prov_profile = ProvisioningProfile(profile=profile, data=prof_bytes)
        return identity, prov_profile
