"""RSA keys, X.509 certificates and PKCS#10 requests."""

from collections.abc import Sequence
import datetime
import hashlib
import re
import secrets
from typing import Any

from iosc.core.errors import PkiError
from iosc.formats import asn1

OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
OID_SHA256_WITH_RSA = "1.2.840.113549.1.1.11"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_EC_PUBLIC_KEY = "1.2.840.10045.2.1"

OID_COMMON_NAME = "2.5.4.3"
OID_COUNTRY = "2.5.4.6"
OID_ORGANIZATION = "2.5.4.10"
OID_ORGANIZATIONAL_UNIT = "2.5.4.11"
OID_EMAIL = "1.2.840.113549.1.9.1"

NAME_ATTRIBUTES = {
    "CN": OID_COMMON_NAME,
    "C": OID_COUNTRY,
    "O": OID_ORGANIZATION,
    "OU": OID_ORGANIZATIONAL_UNIT,
    "emailAddress": OID_EMAIL,
}
ATTRIBUTE_NAMES = {oid: label for label, oid in NAME_ATTRIBUTES.items()}

DEFAULT_KEY_BITS = 2048
PUBLIC_EXPONENT = 65537
MILLER_RABIN_ROUNDS = 64

SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251,
]

SHA256_DIGEST_INFO = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)

PEM_PATTERN = re.compile(
    rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.DOTALL
)


def sha256_algorithm() -> bytes:
    return asn1.encode_sequence(asn1.encode_oid(OID_SHA256))


def rsa_algorithm() -> bytes:
    return asn1.encode_sequence(asn1.encode_oid(OID_RSA_ENCRYPTION),
                                asn1.encode_null())


def sha256_with_rsa_algorithm() -> bytes:
    return asn1.encode_sequence(asn1.encode_oid(OID_SHA256_WITH_RSA),
                                asn1.encode_null())


def encode_pem(label: str, der: bytes) -> str:
    import base64
    body = base64.b64encode(der).decode("ascii")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def decode_pem(text: str | bytes, label: str | None = None) -> bytes:
    import base64
    if isinstance(text, str):
        text = text.encode("ascii", "replace")
    for match in PEM_PATTERN.finditer(text):
        if label is None or match.group(1).decode("ascii") == label:
            return base64.b64decode(re.sub(rb"\s", b"", match.group(2)))
    raise PkiError(f"no {label or 'PEM'} block found")


def _is_probable_prime(candidate: int) -> bool:
    for prime in SMALL_PRIMES:
        if candidate == prime:
            return True
        if candidate % prime == 0:
            return False
    remainder = candidate - 1
    power = 0
    while remainder % 2 == 0:
        remainder //= 2
        power += 1
    for _ in range(MILLER_RABIN_ROUNDS):
        base = secrets.randbelow(candidate - 3) + 2
        witness = pow(base, remainder, candidate)
        if witness in (1, candidate - 1):
            continue
        for _ in range(power - 1):
            witness = pow(witness, 2, candidate)
            if witness == candidate - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if _is_probable_prime(candidate):
            return candidate


class PrivateKey:
    def __init__(self, n: int, e: int, d: int, p: int, q: int) -> None:
        self.n = n
        self.e = e
        self.d = d
        self.p = p
        self.q = q
        self.dp = d % (p - 1)
        self.dq = d % (q - 1)
        self.qinv = pow(q, -1, p)

    @property
    def size(self) -> int:
        return (self.n.bit_length() + 7) // 8

    @classmethod
    def generate(cls, bits: int = DEFAULT_KEY_BITS) -> "PrivateKey":
        if bits < 1024 or bits % 2:
            raise PkiError(f"key size {bits} is not supported")
        while True:
            p = _generate_prime(bits // 2)
            q = _generate_prime(bits // 2)
            if p == q:
                continue
            modulus = p * q
            if modulus.bit_length() != bits:
                continue
            lam = (p - 1) * (q - 1) // _gcd(p - 1, q - 1)
            if _gcd(PUBLIC_EXPONENT, lam) != 1:
                continue
            return cls(modulus, PUBLIC_EXPONENT, pow(PUBLIC_EXPONENT, -1, lam), p, q)

    def public_numbers(self) -> tuple[int, int]:
        return self.n, self.e

    def sign(self, data: bytes) -> bytes:
        digest_info = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
        padding_length = self.size - len(digest_info) - 3
        if padding_length < 8:
            raise PkiError(f"key of {self.size} bytes is too small to sign")
        block = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
        value = pow(int.from_bytes(block, "big"), self.d, self.n)
        return value.to_bytes(self.size, "big")

    def public_key_info(self) -> bytes:
        rsa_public = asn1.encode_sequence(asn1.encode_integer(self.n),
                                          asn1.encode_integer(self.e))
        return asn1.encode_sequence(rsa_algorithm(),
                                    asn1.encode_bit_string(rsa_public))

    def to_der(self) -> bytes:
        return asn1.encode_sequence(
            asn1.encode_integer(0), asn1.encode_integer(self.n),
            asn1.encode_integer(self.e), asn1.encode_integer(self.d),
            asn1.encode_integer(self.p), asn1.encode_integer(self.q),
            asn1.encode_integer(self.dp), asn1.encode_integer(self.dq),
            asn1.encode_integer(self.qinv),
        )

    def to_pkcs8_der(self) -> bytes:
        return asn1.encode_sequence(
            asn1.encode_integer(0), rsa_algorithm(),
            asn1.encode_octet_string(self.to_der()))

    def to_pem(self) -> str:
        return encode_pem("RSA PRIVATE KEY", self.to_der())

    @classmethod
    def from_der(cls, der: bytes) -> "PrivateKey":
        node = asn1.parse_one(der)
        parts = node.children
        if len(parts) == 3 and parts[1].tag == asn1.SEQUENCE:
            return cls.from_der(parts[2].value)
        if len(parts) < 6:
            raise PkiError(f"RSAPrivateKey has {len(parts)} members, expected 9")
        version = parts[0].as_int()
        if version != 0:
            raise PkiError(f"multi-prime keys are not supported (version {version})")
        return cls(parts[1].as_int(), parts[2].as_int(), parts[3].as_int(),
                   parts[4].as_int(), parts[5].as_int())

    @classmethod
    def from_pem(cls, text: str | bytes) -> "PrivateKey":
        try:
            return cls.from_der(decode_pem(text, "RSA PRIVATE KEY"))
        except PkiError:
            return cls.from_der(decode_pem(text, "PRIVATE KEY"))


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def verify_rsa_signature(n: int, e: int, data: bytes, signature: bytes) -> bool:
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    block = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest_info = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    expected = (b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3)
                + b"\x00" + digest_info)
    return secrets.compare_digest(block, expected)


def encode_name(attributes: Sequence[tuple[str, str]]) -> bytes:
    rdns = []
    for label, value in attributes:
        oid = NAME_ATTRIBUTES.get(label, label)
        encoded = (asn1.encode_printable(value)
                   if _is_printable(value) else asn1.encode_utf8(value))
        rdns.append(asn1.encode_set(
            asn1.encode_sequence(asn1.encode_oid(oid), encoded)))
    return asn1.encode_sequence(*rdns)


def _is_printable(value: str) -> bool:
    allowed = set(" '()+,-./:=?")
    return all(c.isalnum() and c.isascii() or c in allowed for c in value)


def decode_name(node: Any) -> list[tuple[str, str]]:
    attributes: list[tuple[str, str]] = []
    for rdn in node.children:
        for pair in rdn.children:
            oid = pair.children[0].as_oid()
            try:
                value = pair.children[1].as_text()
            except asn1.Asn1Error:
                value = pair.children[1].value.decode("utf-8", "replace")
            attributes.append((ATTRIBUTE_NAMES.get(oid, oid), value))
    return attributes


def _parse_time(node: Any) -> datetime.datetime:
    text = node.value.decode("ascii")
    if node.tag == asn1.UTC_TIME:
        year = int(text[:2])
        text = ("19" if year >= 50 else "20") + text
    if not text.endswith("Z"):
        raise PkiError(f"unsupported time zone in {text!r}")
    return datetime.datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(
        tzinfo=datetime.timezone.utc)


class Certificate:
    def __init__(self, der: bytes) -> None:
        self.der = der
        node = asn1.parse_one(der)
        tbs = node.children[0]
        self.tbs_der = tbs.raw
        parts = tbs.children
        index = 0
        self.version = 1
        if parts[0].context_number == 0:
            self.version = parts[0].children[0].as_int() + 1
            index = 1
        self.serial = parts[index].as_int()
        self.signature_algorithm = parts[index + 1].children[0].as_oid()
        self.issuer_der = parts[index + 2].raw
        self.issuer = decode_name(parts[index + 2])
        validity = parts[index + 3].children
        self.not_before = _parse_time(validity[0])
        self.not_after = _parse_time(validity[1])
        self.subject_der = parts[index + 4].raw
        self.subject = decode_name(parts[index + 4])
        self.public_key_der = parts[index + 5].raw
        self.key_algorithm = parts[index + 5].children[0].children[0].as_oid()
        self.n: int | None = None
        self.e: int | None = None
        if self.key_algorithm == OID_RSA_ENCRYPTION:
            rsa_public = asn1.parse_one(parts[index + 5].children[1].as_bit_string())
            self.n = rsa_public.children[0].as_int()
            self.e = rsa_public.children[1].as_int()
        self.signature = node.children[2].as_bit_string()

    @property
    def is_rsa(self) -> bool:
        return self.n is not None

    @classmethod
    def from_pem(cls, text: str | bytes) -> "Certificate":
        return cls(decode_pem(text, "CERTIFICATE"))

    def to_pem(self) -> str:
        return encode_pem("CERTIFICATE", self.der)

    def attribute(self, label: str) -> str | None:
        for name, value in self.subject:
            if name == label:
                return value
        return None

    @property
    def common_name(self) -> str | None:
        return self.attribute("CN")

    @property
    def team_id(self) -> str | None:
        return self.attribute("OU") or self.attribute("0.9.2342.19200300.100.1.1")

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return not (self.not_before <= now <= self.not_after)

    def days_remaining(self, now: datetime.datetime | None = None) -> int:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return (self.not_after - now).days

    def matches(self, key: PrivateKey) -> bool:
        return self.is_rsa and self.n == key.n and self.e == key.e

    def verify(self, data: bytes, signature: bytes) -> bool:
        if not self.is_rsa:
            return False
        assert self.n is not None and self.e is not None
        return verify_rsa_signature(self.n, self.e, data, signature)

    def issuer_and_serial(self) -> bytes:
        return asn1.encode_sequence(self.issuer_der,
                                    asn1.encode_integer(self.serial))

    def __repr__(self) -> str:
        return f"<Certificate {self.common_name!r} serial={self.serial:x}>"


class SigningIdentity:
    def __init__(
        self,
        key: PrivateKey,
        certificate: Certificate,
        intermediates: Sequence[Certificate] | None = None,
    ) -> None:
        if not certificate.matches(key):
            raise PkiError("certificate does not belong to this private key")
        self.key = key
        self.certificate = certificate
        # caller supplies the issuer chain. see iosc.codesign.identity
        self.intermediates = list(intermediates or [])

    @property
    def chain(self) -> list[Certificate]:
        return [self.certificate] + self.intermediates

    @property
    def team_id(self) -> str | None:
        return self.certificate.team_id

    @property
    def common_name(self) -> str | None:
        return self.certificate.common_name

    def problems(self, now: datetime.datetime | None = None) -> list[str]:
        found = []
        if self.certificate.is_expired(now):
            found.append(
                f"certificate expired on {self.certificate.not_after.date()}")
        if self.key.n.bit_length() < 2048:
            found.append(f"key is only {self.key.n.bit_length()} bits")
        return found

    def __repr__(self) -> str:
        return f"<SigningIdentity {self.common_name!r} team={self.team_id}>"


def load_certificate(path: str) -> Certificate:
    with open(path, "rb") as f:
        data = f.read()
    if b"-----BEGIN" in data:
        return Certificate(decode_pem(data, "CERTIFICATE"))
    return Certificate(data)


OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_EXTENDED_KEY_USAGE = "2.5.29.37"
OID_CODE_SIGNING = "1.3.6.1.5.5.7.3.3"


def build_self_signed_certificate(
    key: PrivateKey,
    subject: Sequence[tuple[str, str]],
    days: int = 365,
    serial: int | None = None,
    not_before: datetime.datetime | None = None,
) -> bytes:
    not_before = not_before or datetime.datetime.now(datetime.timezone.utc)
    not_after = not_before + datetime.timedelta(days=days)
    name = encode_name(subject)
    extensions = asn1.encode_sequence(
        asn1.encode_sequence(asn1.encode_oid(OID_BASIC_CONSTRAINTS),
                             asn1.encode_boolean(True),
                             asn1.encode_octet_string(asn1.encode_sequence())),
        asn1.encode_sequence(asn1.encode_oid(OID_KEY_USAGE),
                             asn1.encode_boolean(True),
                             asn1.encode_octet_string(
                                 asn1.encode_bit_string(b"\x80", 7))),
        asn1.encode_sequence(asn1.encode_oid(OID_EXTENDED_KEY_USAGE),
                             asn1.encode_octet_string(asn1.encode_sequence(
                                 asn1.encode_oid(OID_CODE_SIGNING)))),
    )
    tbs = asn1.encode_sequence(
        asn1.encode_explicit(0, asn1.encode_integer(2)),
        asn1.encode_integer(serial if serial is not None
                            else secrets.randbits(64) | 1),
        sha256_with_rsa_algorithm(),
        name,
        asn1.encode_sequence(asn1.encode_utc_time(not_before),
                             asn1.encode_utc_time(not_after)),
        name,
        key.public_key_info(),
        asn1.encode_explicit(3, extensions),
    )
    return asn1.encode_sequence(tbs, sha256_with_rsa_algorithm(),
                                asn1.encode_bit_string(key.sign(tbs)))


def build_certificate_request(
    key: PrivateKey,
    subject: Sequence[tuple[str, str]],
) -> bytes:
    info = asn1.encode_sequence(
        asn1.encode_integer(0),
        encode_name(subject),
        key.public_key_info(),
        asn1.encode(asn1.context(0), b""),
    )
    return asn1.encode_sequence(info, sha256_with_rsa_algorithm(),
                                asn1.encode_bit_string(key.sign(info)))


def certificate_request_pem(
    key: PrivateKey,
    subject: Sequence[tuple[str, str]],
) -> str:
    return encode_pem("CERTIFICATE REQUEST",
                      build_certificate_request(key, subject))


def verify_certificate_request(der: bytes) -> bool:
    node = asn1.parse_one(der)
    info = node.children[0]
    rsa_public = asn1.parse_one(info.children[2].children[1].as_bit_string())
    n = rsa_public.children[0].as_int()
    e = rsa_public.children[1].as_int()
    return verify_rsa_signature(n, e, info.raw, node.children[2].as_bit_string())
