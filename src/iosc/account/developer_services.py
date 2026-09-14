import datetime
import time
from typing import Any
import uuid

from iosc.account.grandslam import Client, login_with_cache
from iosc.account.provisioning import download_profile
from iosc.account.types import ProvisioningProfile, IssuedCertificate
from iosc.config import cache
from iosc.core import get_reporter
from iosc.core.errors import (
    AuthError,
    CertificateLimitError,
    DevServicesError,
    FormatError,
    GrandSlamError,
)
from iosc.formats import pki
from iosc.formats.mobileprovision import Profile

CLIENT_ID = "XABBG36SBA"
PROTOCOL_VERSION = "QH65B2"
PORTAL = f"https://developerservices2.apple.com/services/{PROTOCOL_VERSION}"
MACHINE_NAME = "iosc"
LOCALE = "en_US"

PLATFORMS = {"ios": "ios/", "tvos": "tvos/", "watchos": "watchos/", "any": ""}

FREE_ACCOUNT_LIMIT = 3840
NO_TEAMS = 1100

CERT_LIMIT_CODES = {3245, 3246, 3840, 1200}


def _is_cert_limit_error(error: Any) -> bool:
    code = getattr(error, "code", 0)
    if code in CERT_LIMIT_CODES:
        return True
    msg = str(error).lower()
    phrases = (
        "too many certificates",
        "maximum number of certificates",
        "development-certificate limit",
        "development certificate limit",
        "certificate limit",
        "maximum allowed certificates",
        "already have a current",
    )
    if any(p in msg for p in phrases):
        return True
    if ("limit" in msg or "maximum" in msg) and "certificate" in msg:
        return True
    return False


def _cert_sort_key(c: Any) -> float:
    cert = getattr(c, "certificate", None)
    nb = getattr(cert, "not_before", None)
    if isinstance(nb, datetime.datetime):
        if nb.tzinfo is None:
            nb = nb.replace(tzinfo=datetime.timezone.utc)
        return nb.timestamp()
    return 0.0


class Team:
    def __init__(self, data: dict[str, Any]) -> None:
        self.name = data.get("name", "")
        self.team_id = data["teamId"]
        self.kind = data.get("type", "")

    @property
    def is_free(self) -> bool:
        return self.kind.lower() == "individual" or self.kind == ""

    def __repr__(self) -> str:
        return f"Team({self.team_id}, {self.name!r})"


class Device:
    def __init__(self, data: dict[str, Any]) -> None:
        self.device_id = data["deviceId"]
        self.name = data.get("name", "")
        self.udid = data.get("deviceNumber", "")


class Certificate:
    def __init__(self, data: dict[str, Any]) -> None:
        self.certificate_id = data.get("certificateId", "")
        self.name = data.get("name", "")
        self.serial = data.get("serialNumber", "")
        self.machine_name = data.get("machineName", "")
        self.certificate = pki.Certificate(bytes(data["certContent"]))

    def matches(self, key: pki.PrivateKey) -> bool:
        return self.certificate.matches(key)


class AppId:
    def __init__(self, data: dict[str, Any]) -> None:
        self.app_id_id = data["appIdId"]
        self.identifier = data["identifier"]
        self.name = data.get("name", "")
        self.expires = data.get("expirationDate")


class DeveloperSession:
    def __init__(
        self,
        account: Any,
        cache_dir: str | None = None,
        login_callable: Any | None = None,
    ) -> None:
        self.account = account
        self.cache_dir = cache_dir
        self.login_callable = login_callable

    def _is_auth_error(self, err: Any) -> bool:
        code = getattr(err, "code", 0)
        if code in (401, 403, -22411):
            return True
        msg = str(err).lower()
        auth_keywords = (
            "unauthorized",
            "forbidden",
            "authentication",
            "authorization",
            "session expired",
            "session has expired",
            "token expired",
            "token has expired",
            "invalid token",
            "rejected token",
            "auth error",
            "auth failure",
            "expired or rejected token",
        )
        return any(k in msg for k in auth_keywords)

    def _refresh_account(self) -> bool:
        apple_id = getattr(self.account, "apple_id", None)
        if not apple_id:
            return False
        cache.invalidate_app_token(apple_id, root_dir=self.cache_dir)
        client = getattr(self.account, "client", None) or Client()
        login_func = self.login_callable or getattr(self.account, "login_callable", None)
        password = getattr(self.account, "password", None)
        try:
            self.account = login_with_cache(
                client,
                apple_id,
                password=password,
                cache_dir=self.cache_dir,
                login_callable=login_func,
            )
            return True
        except (AuthError, GrandSlamError):
            return False

    def request(
        self,
        action: str,
        parameters: dict[str, Any] | None = None,
        platform: str = "ios",
        _retried: bool = False,
    ) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise DevServicesError(f"unknown platform {platform}")
        url = f"{PORTAL}/{PLATFORMS[platform]}{action}?clientId={CLIENT_ID}"
        body: dict[str, Any] = {
            "clientId": CLIENT_ID,
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": str(uuid.uuid4()).upper(),
            "userLocale": [LOCALE],
        }
        body.update(parameters or {})
        try:
            response = self.account.post(url, body)
            if not isinstance(response, dict):
                raise DevServicesError(f"{action} answered with non dictionary response")
            code = int(response.get("resultCode", 0) or 0)
            if code:
                message = (
                    response.get("userString")
                    or response.get("resultString")
                    or f"error {code}"
                )
                raise DevServicesError(message, code)
            return response
        except (DevServicesError, GrandSlamError) as error:
            if not _retried and self._is_auth_error(error):
                if self._refresh_account():
                    return self.request(action, parameters, platform=platform, _retried=True)
            raise

    def _request(
        self,
        action: str,
        parameters: dict[str, Any] | None = None,
        platform: str = "ios",
        _retried: bool = False,
    ) -> dict[str, Any]:
        return self.request(action, parameters, platform=platform, _retried=_retried)

    def teams(self) -> list[Team]:
        teams = [
            Team(entry)
            for entry in self.request("listTeams.action", platform="any").get("teams", [])
        ]
        if not teams:
            raise DevServicesError(
                "this Apple ID belongs to no team, so it cannot sign anything",
                NO_TEAMS,
            )
        return teams

    def team(self, team_id: str | None = None) -> Team:
        teams = self.teams()
        if team_id is None:
            return teams[0]
        for candidate in teams:
            if candidate.team_id == team_id:
                return candidate
        known = ", ".join(t.team_id for t in teams)
        raise DevServicesError(f"no team {team_id} on this account, only {known}")

    def devices(self, team: Team) -> list[Device]:
        return [
            Device(entry)
            for entry in self.request(
                "listDevices.action",
                {"teamId": team.team_id},
            ).get("devices", [])
        ]

    def add_device(self, team: Team, name: str, udid: str) -> Device:
        response = self.request("addDevice.action", {
            "teamId": team.team_id,
            "name": name,
            "deviceNumber": udid,
        })
        dev = Device(response["device"])
        current = cache.get_devices(root_dir=self.cache_dir) or []
        current.append({
            "deviceId": dev.device_id,
            "name": dev.name,
            "deviceNumber": dev.udid,
        })
        cache.set_devices(current, root_dir=self.cache_dir)
        return dev

    def ensure_device(self, team: Team, udid: str, name: str | None = None) -> Device:
        udid_lower = udid.lower()
        for device in self.devices(team):
            if device.udid.lower() == udid_lower:
                return device
        return self.add_device(team, name or f"{MACHINE_NAME} device", udid)

    def certificates(self, team: Team) -> list[Certificate]:
        rows = self.request(
            "listAllDevelopmentCerts.action",
            {"teamId": team.team_id},
        ).get("certificates", [])
        parsed: list[Certificate] = []
        for entry in rows:
            try:
                parsed.append(Certificate(entry))
            except (FormatError, KeyError, ValueError):
                # apple briefly serves an error page in certContent for a fresh row
                # a later listing has the real cert
                get_reporter().detail(
                    f"skipping unreadable certificate row {entry.get('certificateId', '?')}"
                )
        return parsed

    def submit_csr(self, team: Team, csr_pem: str) -> str:
        response = self.request("submitDevelopmentCSR.action", {
            "teamId": team.team_id,
            "machineId": str(uuid.uuid4()).upper(),
            "machineName": MACHINE_NAME,
            "csrContent": csr_pem,
        })
        return response["certRequest"]["certRequestId"]

    def revoke_certificate(self, team: Team, certificate: Certificate) -> None:
        self.request("revokeDevelopmentCert.action", {
            "teamId": team.team_id,
            "serialNumber": certificate.serial,
        })

    def certificate_for(self, team: Team, key: pki.PrivateKey) -> Certificate | None:
        for certificate in self.certificates(team):
            if certificate.matches(key):
                return certificate
        return None

    def obtain_certificate(
        self,
        team: Team,
        key: pki.PrivateKey | None = None,
        revoke_and_reissue: bool = False,
    ) -> tuple[IssuedCertificate, Certificate]:
        cached = cache.get_certificate(team.team_id, root_dir=self.cache_dir)
        if cached is not None:
            pem_bytes, key_bytes = cached.pem, cached.key
            cert = (
                pki.Certificate.from_pem(pem_bytes)
                if b"-----BEGIN" in pem_bytes
                else pki.Certificate(pem_bytes)
            )
            priv_key = pki.PrivateKey.from_pem(key_bytes)
            if (key is None or cert.matches(key)) and cache.is_certificate_valid(cert):
                cert_obj = Certificate({
                    "certContent": cert.der,
                    "name": cert.common_name or MACHINE_NAME,
                    "serialNumber": hex(cert.serial),
                })
                identity = IssuedCertificate(
                    certificate=cert.der,
                    private_key=(key or priv_key).to_pem().encode("ascii"),
                    team_id=team.team_id,
                )
                return identity, cert_obj

        if key is not None:
            existing = self.certificate_for(team, key)
            if existing is not None:
                cache.set_certificate(
                    team.team_id,
                    existing.certificate.der,
                    key.to_pem().encode("ascii"),
                    root_dir=self.cache_dir,
                    expiry=int(existing.certificate.not_after.timestamp()),
                )
                identity = IssuedCertificate(
                    certificate=existing.certificate.der,
                    private_key=key.to_pem().encode("ascii"),
                    team_id=team.team_id,
                )
                return identity, existing
        else:
            cached_key_bytes = cache.get_private_key(team.team_id, root_dir=self.cache_dir)
            if cached_key_bytes is not None:
                cached_key = pki.PrivateKey.from_pem(cached_key_bytes)
                existing = self.certificate_for(team, cached_key)
                if existing is not None and cache.is_certificate_valid(existing.certificate):
                    cache.set_certificate(
                        team.team_id,
                        existing.certificate.der,
                        cached_key.to_pem().encode("ascii"),
                        root_dir=self.cache_dir,
                        expiry=int(existing.certificate.not_after.timestamp()),
                    )
                    identity = IssuedCertificate(
                        certificate=existing.certificate.der,
                        private_key=cached_key.to_pem().encode("ascii"),
                        team_id=team.team_id,
                    )
                    return identity, existing

        if key is None:
            key = pki.PrivateKey.generate(2048)
        subject = [("CN", MACHINE_NAME), ("C", "US")]
        csr_pem = pki.certificate_request_pem(key, subject)
        try:
            self.submit_csr(team, csr_pem)
        except DevServicesError as error:
            if not _is_cert_limit_error(error):
                raise
            existing_certs = self.certificates(team)
            if revoke_and_reissue and existing_certs:
                oldest = min(existing_certs, key=_cert_sort_key)
                self.revoke_certificate(team, oldest)
                self.submit_csr(team, csr_pem)
            else:
                cert_lines = "\n".join(
                    f"  - {c.name or 'Certificate'} (serial: {c.serial}, id: {c.certificate_id})"
                    for c in existing_certs
                )
                remedy = "Revoke an existing certificate using revoke_certificate(team, cert) to free a slot."
                msg = (
                    f"account is at the development-certificate limit ({len(existing_certs)} existing):\n"
                    f"{cert_lines}\n{remedy}"
                )
                raise CertificateLimitError(msg, certificates=existing_certs, code=error.code) from error

        issued = self.certificate_for(team, key)
        for _ in range(6):
            if issued is not None:
                break
            # new cert row can take a few seconds to become readable
            time.sleep(2)
            issued = self.certificate_for(team, key)
        if issued is None:
            raise DevServicesError(
                "Apple accepted the certificate request but did not return a certificate for it"
            )
        cache.set_certificate(
            team.team_id,
            issued.certificate.der,
            key.to_pem().encode("ascii"),
            root_dir=self.cache_dir,
            expiry=int(issued.certificate.not_after.timestamp()),
        )
        identity = IssuedCertificate(
            certificate=issued.certificate.der,
            private_key=key.to_pem().encode("ascii"),
            team_id=team.team_id,
        )
        return identity, issued

    def app_ids(self, team: Team) -> dict[str, Any]:
        response = self.request("listAppIds.action", {"teamId": team.team_id})
        return {
            "app_ids": [AppId(entry) for entry in response.get("appIds", [])],
            "maximum": response.get("maxQuantity"),
            "available": response.get("availableQuantity"),
        }

    def add_app_id(self, team: Team, identifier: str, name: str) -> None:
        self.request("addAppId.action", {
            "teamId": team.team_id,
            "identifier": identifier,
            "name": name,
        })

    def ensure_app_id(self, team: Team, identifier: str, name: str | None = None) -> AppId:
        listing = self.app_ids(team)
        for app_id in listing["app_ids"]:
            if app_id.identifier == identifier:
                return app_id
        available = listing["available"]
        if available is not None and int(available) <= 0:
            raise DevServicesError(
                f"this account has no app id slots left. A free account gets {listing['maximum']} of them and they take a week to expire",
                FREE_ACCOUNT_LIMIT,
            )
        self.add_app_id(team, identifier, name or identifier.replace(".", " "))
        for app_id in self.app_ids(team)["app_ids"]:
            if app_id.identifier == identifier:
                return app_id
        raise DevServicesError(f"Apple did not register {identifier}")

    def download_profile(self, team: Team, app_id: AppId) -> Profile:
        return download_profile(self, team.team_id, app_id.app_id_id)

    def obtain_identity_and_profile(
        self,
        bundle_id: str,
        udid: str,
        device_name: str | None = None,
        key: pki.PrivateKey | None = None,
        team_id: str | None = None,
        application_name: str | None = None,
        revoke_and_reissue: bool = False,
    ) -> tuple[IssuedCertificate, ProvisioningProfile]:
        team = self.team(team_id)
        identity, _ = self.obtain_certificate(team, key, revoke_and_reissue=revoke_and_reissue)

        cached_profile_rec = cache.get_profile(bundle_id, udid, root_dir=self.cache_dir)
        sidecar_expiry = cache.get_profile_expiry(bundle_id, udid, root_dir=self.cache_dir)
        if cached_profile_rec is not None and cache.is_profile_valid(cached_profile_rec, sidecar_expiry):
            cached_parsed = Profile.from_bytes(cached_profile_rec.data)
            cert_obj = pki.Certificate(identity.certificate)
            problems = cached_parsed.problems(bundle_id, udid, cert_obj, datetime.datetime.now())
            if not problems:
                return identity, ProvisioningProfile(profile=cached_parsed, data=cached_profile_rec.data)

        self.ensure_device(team, udid, device_name)
        app_id = self.ensure_app_id(team, bundle_id, application_name)
        profile = self.download_profile(team, app_id)
        cert_obj = pki.Certificate(identity.certificate)
        problems = profile.problems(bundle_id, udid, cert_obj, datetime.datetime.now())
        if problems:
            raise DevServicesError(
                "Apple issued a profile that does not fit this build: " + "; ".join(problems)
            )
        expiry_ts = int(profile.expiration_date.timestamp()) if profile.expiration_date else None
        cache.set_profile(bundle_id, udid, profile.data, root_dir=self.cache_dir, expiry=expiry_ts)
        return identity, ProvisioningProfile(profile=profile, data=profile.data)
