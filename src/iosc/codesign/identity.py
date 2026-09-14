from pathlib import Path

from iosc.config import paths
from iosc.core import MissingExternalAsset
from iosc.formats import pki

APPLE_WWDR_ISSUER_CN = "Apple Worldwide Developer Relations Certification Authority"


def issuer_common_name(certificate: pki.Certificate) -> str:
    return next((v for k, v in (certificate.issuer or []) if k == "CN"), "")


def is_apple_issued(certificate: pki.Certificate) -> bool:
    return issuer_common_name(certificate).startswith(APPLE_WWDR_ISSUER_CN)


# the CMS must carry the WWDR intermediate and apple root
# without the full chain the device kills the app at launch
def load_apple_chain() -> list[pki.Certificate]:
    directory = paths.require(paths.apple_certs_dir())
    found: list[pki.Certificate] = []
    missing: list[str] = []
    for name in paths.apple_intermediates():
        candidate = Path(directory) / name
        if candidate.is_file():
            found.append(pki.load_certificate(str(candidate)))
        else:
            missing.append(name)
    if missing:
        raise MissingExternalAsset(
            f"Missing {', '.join(missing)} in {directory}. "
            "README.md names where Apple publishes them."
        )
    return found


def signing_identity(
    key: pki.PrivateKey, certificate: pki.Certificate
) -> pki.SigningIdentity:
    intermediates = load_apple_chain() if is_apple_issued(certificate) else []
    return pki.SigningIdentity(key, certificate, intermediates)


def identity_from_bytes(
    certificate: bytes, private_key: bytes
) -> pki.SigningIdentity:
    parsed = (
        pki.Certificate.from_pem(certificate)
        if b"-----BEGIN" in certificate
        else pki.Certificate(certificate)
    )
    return signing_identity(pki.PrivateKey.from_pem(private_key), parsed)


def identity_from_files(
    key_path: str | Path, certificate_path: str | Path
) -> pki.SigningIdentity:
    key = pki.PrivateKey.from_pem(Path(key_path).read_bytes())
    return signing_identity(key, pki.load_certificate(str(certificate_path)))
