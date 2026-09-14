import hashlib
import hmac
import secrets

from iosc.core import AuthError
from iosc.formats import aes

PBKDF2_LENGTH = 32
EPHEMERAL_BITS = 256
PROTOCOLS = ("s2k", "s2k_fo")

N_1024 = int(
    "EEAF0AB9ADB38DD69C33F80AFA8FC5E86072618775FF3C0B9EA2314C9C256576"
    "D674DF7496EA81D3383B4813D692C6E0E0D5D8E250B98BE48E495C1D6089DAD1"
    "5DC7D7B46154D6B6CE8EF4AD69B15D4982559B297BCF1885C529F566660E57EC"
    "68EDBC3C05726CC02FD4CBF4976EAA9AFD5138FE8376435B9FC61D2FC0EB06E3",
    16,
)

N_2048 = int(
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050"
    "A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50"
    "E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8"
    "55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B"
    "CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748"
    "544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6"
    "AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB6"
    "94B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73",
    16,
)

GROUPS = {1024: (N_1024, 2), 2048: (N_2048, 2)}


def _digest(name: str, *parts: bytes) -> bytes:
    state = hashlib.new(name)
    for part in parts:
        state.update(part)
    return state.digest()


def _pad(value: int, width: int) -> bytes:
    return value.to_bytes(width, "big")


def _minimal(value: int) -> bytes:
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


class SrpClient:
    def __init__(
        self,
        group: int = 2048,
        hash_name: str = "sha256",
        secret: int | None = None,
    ) -> None:
        if group not in GROUPS:
            raise AuthError(f"no {group} bit group")
        self.n, self.g = GROUPS[group]
        self.hash_name = hash_name
        self.width = (self.n.bit_length() + 7) // 8
        self.secret = secrets.randbits(EPHEMERAL_BITS) if secret is None else secret
        self.public = pow(self.g, self.secret, self.n)
        self.k = int.from_bytes(
            _digest(hash_name, _pad(self.n, self.width), _pad(self.g, self.width)),
            "big",
        )

    def compute_x(self, identity: bytes, password: bytes, salt: bytes) -> int:
        inner = _digest(self.hash_name, identity, b":", password)
        return int.from_bytes(_digest(self.hash_name, salt, inner), "big")

    def verifier(self, x: int) -> int:
        return pow(self.g, x, self.n)

    def scramble(self, server_public: int) -> int:
        return int.from_bytes(
            _digest(
                self.hash_name,
                _pad(self.public, self.width),
                _pad(server_public, self.width),
            ),
            "big",
        )

    def shared_secret(self, x: int, server_public: int | bytes | bytearray) -> int:
        if isinstance(server_public, (bytes, bytearray)):
            server_public = int.from_bytes(server_public, "big")
        if server_public % self.n == 0:
            raise AuthError("the server sent a public value of zero")
        u = self.scramble(server_public)
        if u == 0:
            raise AuthError("the scrambling parameter came out zero")
        base = (server_public - self.k * self.verifier(x)) % self.n
        return pow(base, self.secret + u * x, self.n)


def derive_password_key(
    password: str, protocol: str, salt: bytes, iterations: int
) -> bytes:
    if protocol not in PROTOCOLS:
        raise AuthError(f"the server chose protocol {protocol!r}, which is unknown")
    if iterations < 1:
        raise AuthError("iteration count must be positive")
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    if protocol == "s2k_fo":
        digest = digest.hex().encode("ascii")
    return hashlib.pbkdf2_hmac("sha256", digest, salt, iterations, PBKDF2_LENGTH)


class AppleSrpSession:
    def __init__(self, secret: int | None = None) -> None:
        self.client = SrpClient(2048, "sha256", secret)
        self.public = _minimal(self.client.public)
        self.key: bytes | None = None
        self.proof: bytes | None = None

    def start(self) -> bytes:
        return self.public

    def respond(
        self,
        apple_id: str,
        password: str,
        protocol: str,
        server_public: bytes,
        salt: bytes,
        iterations: int,
    ) -> bytes:
        password_key = derive_password_key(password, protocol, salt, iterations)
        x = self.client.compute_x(b"", password_key, salt)
        shared = self.client.shared_secret(x, server_public)
        self.key = hashlib.sha256(_minimal(shared)).digest()

        width = self.client.width
        group_hash = bytes(
            a ^ b
            for a, b in zip(
                hashlib.sha256(_pad(self.client.n, width)).digest(),
                hashlib.sha256(_pad(self.client.g, width)).digest(),
            )
        )
        self.proof = _digest(
            "sha256",
            group_hash,
            hashlib.sha256(apple_id.encode("utf-8")).digest(),
            salt,
            self.public,
            server_public,
            self.key,
        )
        return self.proof

    def verify(self, server_proof: bytes) -> bool:
        if self.key is None or self.proof is None:
            raise AuthError("nothing to verify before responding")
        expected = _digest("sha256", self.public, self.proof, self.key)
        return hmac.compare_digest(expected, server_proof)

    def _derived(self, label: bytes) -> bytes:
        if self.key is None:
            raise AuthError("no session key yet")
        return hmac.new(self.key, label, hashlib.sha256).digest()

    def decrypt_session_data(self, blob: bytes) -> bytes:
        if self.key is None:
            raise AuthError("no session key yet")
        plain = aes.decrypt_cbc(
            self._derived(b"extra data key:"),
            self._derived(b"extra data iv:")[: aes.BLOCK_SIZE],
            blob,
            padding=False,
        )
        try:
            return aes.unpad(plain)
        except aes.AesError:
            return plain
