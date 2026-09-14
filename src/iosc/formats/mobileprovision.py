from dataclasses import dataclass
import datetime
from pathlib import Path
from typing import Any, Sequence

from iosc.core.errors import MobileprovisionError
from iosc.formats import asn1
from iosc.formats.pki import Certificate
from iosc.formats.plist import read_plist

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"

PROFILE_NAME = "embedded.mobileprovision"
FREE_ACCOUNT_MAX_DAYS = 8


def extract_plist(data: bytes) -> bytes:
    if data[:5] == b"<?xml" or data[:6] == b"bplist":
        return data
    try:
        root = asn1.parse_one(data)
    except asn1.Asn1Error as exc:
        raise MobileprovisionError(f"not a plist and not parseable as CMS ({exc})") from exc
    wrappers = asn1.find_oid(root, OID_SIGNED_DATA)
    if not wrappers:
        raise MobileprovisionError("no CMS SignedData in profile")
    signed_data = wrappers[0].children[1].children[0]
    content = signed_data.children[2].children
    if len(content) < 2:
        raise MobileprovisionError("profile has detached signature and carries no plist")
    return content[1].children[0].value


@dataclass(frozen=True)
class Profile:
    data: bytes
    name: str | None
    uuid: str | None
    team_id: str | None
    team_name: str | None
    platforms: tuple[str, ...]
    entitlements: dict[str, Any]
    devices: tuple[str, ...]
    all_devices: bool
    creation_date: datetime.datetime | None
    expiration_date: datetime.datetime | None
    certificates: tuple[Certificate, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "Profile":
        plist_bytes = extract_plist(data)
        plist = read_plist(plist_bytes)
        team_id = (plist.get("TeamIdentifier") or [None])[0]
        certs = tuple(
            Certificate(der)
            for der in (plist.get("DeveloperCertificates") or [])
        )
        platforms = tuple(plist.get("Platform") or [])
        devices = tuple(plist.get("ProvisionedDevices") or [])
        entitlements = dict(plist.get("Entitlements") or {})

        return cls(
            data=data,
            name=plist.get("Name"),
            uuid=plist.get("UUID"),
            team_id=team_id,
            team_name=plist.get("TeamName"),
            platforms=platforms,
            entitlements=entitlements,
            devices=devices,
            all_devices=bool(plist.get("ProvisionsAllDevices")),
            creation_date=plist.get("CreationDate"),
            expiration_date=plist.get("ExpirationDate"),
            certificates=certs,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "Profile":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    @property
    def application_identifier(self) -> str | None:
        return (
            self.entitlements.get("application-identifier")
            or self.entitlements.get("com.apple.application-identifier")
        )

    @property
    def bundle_id(self) -> str | None:
        identifier = self.application_identifier
        if not identifier:
            return None
        prefix = f"{self.team_id}." if self.team_id else ""
        return identifier[len(prefix):] if identifier.startswith(prefix) else identifier

    @property
    def is_wildcard(self) -> bool:
        return (self.bundle_id or "").endswith("*")

    @property
    def lifetime_days(self) -> int | None:
        if not self.creation_date or not self.expiration_date:
            return None
        return (self.expiration_date - self.creation_date).days

    @property
    def is_free_account(self) -> bool:
        lifetime = self.lifetime_days
        return lifetime is not None and lifetime <= FREE_ACCOUNT_MAX_DAYS

    def days_remaining(self, now: datetime.datetime | None = None) -> int | None:
        if not self.expiration_date:
            return None
        current = now or datetime.datetime.now()
        expiry = self.expiration_date
        if expiry.tzinfo and not current.tzinfo:
            current = current.replace(tzinfo=expiry.tzinfo)
        if current.tzinfo and not expiry.tzinfo:
            expiry = expiry.replace(tzinfo=current.tzinfo)
        return (expiry - current).days

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        remaining = self.days_remaining(now)
        return remaining is not None and remaining < 0

    def matches_bundle_id(self, bundle_id: str) -> bool:
        pattern = self.bundle_id
        if not pattern:
            return False
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            return bundle_id.startswith(pattern[:-1])
        return pattern == bundle_id

    def allows_device(self, udid: str) -> bool:
        return self.all_devices or udid.lower() in {d.lower() for d in self.devices}

    def matches_certificate(self, certificate: Certificate) -> bool:
        return any(c.der == certificate.der for c in self.certificates)

    def entitlements_for(self, bundle_id: str) -> dict[str, Any]:
        entitlements = dict(self.entitlements)
        identifier = self.application_identifier
        if identifier and identifier.endswith("*"):
            prefix = f"{self.team_id}." if self.team_id else ""
            entitlements["application-identifier"] = prefix + bundle_id
        entitlements.pop("com.apple.developer.team-identifier", None)
        if self.team_id:
            entitlements["com.apple.developer.team-identifier"] = self.team_id
        return entitlements

    def problems(
        self,
        bundle_id: str | None = None,
        udid: str | None = None,
        certificate: Certificate | None = None,
        now: datetime.datetime | None = None,
    ) -> list[str]:
        found: list[str] = []
        if self.is_expired(now):
            found.append(f"profile expired on {self.expiration_date}")
        if bundle_id and not self.matches_bundle_id(bundle_id):
            found.append(f"profile covers {self.bundle_id!r}, not {bundle_id!r}")
        if udid and not self.allows_device(udid):
            found.append(f"device {udid} is not in the profile's {len(self.devices)} devices")
        if certificate and not self.matches_certificate(certificate):
            found.append("signing certificate is not one the profile lists")
        if not self.team_id:
            found.append("profile carries no team identifier")
        return found

    def warnings(self, now: datetime.datetime | None = None) -> list[str]:
        notes: list[str] = []
        remaining = self.days_remaining(now)
        if self.is_free_account:
            notes.append(
                "This profile came from a free Apple ID. It is unofficial and "
                "unsupported by Apple, the app stops launching when the profile "
                "expires, and it has to be re-signed and reinstalled weekly."
            )
        if remaining is not None and 0 <= remaining <= 7:
            notes.append(f"profile expires in {remaining} day{'' if remaining == 1 else 's'}")
        if not self.devices and not self.all_devices:
            notes.append("profile lists no devices, so nothing can install the result")
        return notes

    def summary(self) -> str:
        remaining = self.days_remaining()
        devices = "all" if self.all_devices else str(len(self.devices))
        return (
            f"{self.name!r} team {self.team_id} app {self.bundle_id} "
            f"devices {devices} expires in {remaining} days"
        )


def select_profile(
    profiles: Sequence[Profile],
    bundle_id: str,
    certificate: Certificate | None = None,
    udid: str | None = None,
    now: datetime.datetime | None = None,
) -> Profile | None:
    usable = [
        p for p in profiles
        if not p.problems(bundle_id, udid, certificate, now)
    ]
    if not usable:
        return None
    return sorted(usable, key=lambda p: (p.is_wildcard, -(p.days_remaining(now) or 0)))[0]
