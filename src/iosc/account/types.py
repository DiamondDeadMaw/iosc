from dataclasses import dataclass

from iosc.formats.mobileprovision import Profile


@dataclass(frozen=True)
class IssuedCertificate:
    certificate: bytes
    private_key: bytes
    team_id: str


@dataclass(frozen=True)
class ProvisioningProfile:
    profile: Profile
    data: bytes
