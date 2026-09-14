import datetime
import os
import socket
import ssl
import tempfile
import uuid
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from iosc.core.errors import DeviceError
from iosc.device import transport
from iosc.device.transport import DeviceInfo, PlistConnection

LOCKDOWN_PORT = 62078
LOCKDOWN_TYPE = "com.apple.mobile.lockdown"
LABEL = "iosc"

PAIRING_PENDING_ERROR = "PairingDialogResponsePending"
PAIRING_DENIED_ERRORS = ("UserDeniedPairing", "UserDenied")


class LockdownProtocolError(DeviceError):
    def __init__(self, message: str, error: str | None = None) -> None:
        super().__init__(message)
        self.error = error


class PairingPending(LockdownProtocolError):
    pass


class PairingDenied(LockdownProtocolError):
    pass


class SessionInactive(DeviceError):
    pass


class LockdownClient:
    def __init__(
        self,
        device: DeviceInfo,
        host: str = transport.USBMUX_HOST,
        mux_port: int = transport.USBMUX_PORT,
    ) -> None:
        self.device = device
        self.host = host
        self.mux_port = mux_port
        self.connection: PlistConnection | None = None
        self.session_id: str | None = None
        self.pair_record: dict[str, Any] | None = None
        self._ssl_temp: list[str] = []

    def connect(self) -> "LockdownClient":
        if self.connection is not None:
            return self
        sock = transport.connect_device(
            self.device.device_id,
            LOCKDOWN_PORT,
            host=self.host,
            mux_port=self.mux_port,
        )
        self.connection = PlistConnection(sock, "lockdown")
        kind = self.query_type()
        if kind != LOCKDOWN_TYPE:
            raise LockdownProtocolError(f"unexpected lockdown service type {kind}")
        return self

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self._cleanup_ssl_temp()

    def __enter__(self) -> "LockdownClient":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.connection is None:
            raise DeviceError("lockdown client is not connected")
        payload = dict(payload)
        payload.setdefault("Label", LABEL)
        payload.setdefault("ProtocolVersion", "2")
        self.connection.send(payload)
        response = self.connection.receive()
        error = response.get("Error")
        if error:
            raise LockdownProtocolError(f"lockdown error {error}", error)
        return response

    def query_type(self) -> str:
        return self._request({"Request": "QueryType"}).get("Type", "")

    def get_value(self, key: str | None = None, domain: str | None = None) -> Any:
        payload: dict[str, Any] = {"Request": "GetValue"}
        if key is not None:
            payload["Key"] = key
        if domain is not None:
            payload["Domain"] = domain
        return self._request(payload).get("Value")

    def udid(self) -> str:
        return self.device.serial or self.get_value("UniqueDeviceID")

    def device_name(self) -> str:
        return self.get_value("DeviceName")

    def product_version(self) -> str:
        return self.get_value("ProductVersion")

    def device_public_key(self) -> bytes:
        return bytes(self.get_value("DevicePublicKey"))

    def system_buid(self) -> str:
        with transport.UsbmuxClient(self.host, self.mux_port) as client:
            return client.read_buid()

    def load_saved_pair_record(self) -> dict[str, Any] | None:
        with transport.UsbmuxClient(self.host, self.mux_port) as client:
            return client.read_pair_record(self.udid())

    def save_pair_record(self, record: dict[str, Any]) -> None:
        with transport.UsbmuxClient(self.host, self.mux_port) as client:
            client.save_pair_record(
                self.udid(), record, device_id=self.device.device_id
            )

    def generate_pair_record(self) -> dict[str, Any]:
        device_key = serialization.load_pem_public_key(self.device_public_key())
        return build_pair_record(device_key, self.system_buid())

    def pair(self, pair_record: dict[str, Any] | None = None) -> dict[str, Any]:
        if pair_record is None:
            pair_record = self.load_saved_pair_record()
        if pair_record is None:
            pair_record = self.generate_pair_record()

        wire_record = {
            "DeviceCertificate": pair_record["DeviceCertificate"],
            "HostCertificate": pair_record["HostCertificate"],
            "RootCertificate": pair_record["RootCertificate"],
            "HostID": pair_record["HostID"],
            "SystemBUID": pair_record["SystemBUID"],
        }
        try:
            response = self._request(
                {
                    "Request": "Pair",
                    "PairRecord": wire_record,
                    "PairingOptions": {"ExtendedPairingErrors": True},
                }
            )
        except LockdownProtocolError as error:
            if error.error == PAIRING_PENDING_ERROR:
                raise PairingPending(
                    "trust dialog is open on the device, tap Trust and retry",
                    error.error,
                ) from error
            if error.error in PAIRING_DENIED_ERRORS:
                raise PairingDenied(
                    "the user declined the trust prompt", error.error
                ) from error
            raise

        escrow = response.get("EscrowBag")
        if escrow is not None:
            pair_record["EscrowBag"] = escrow
        self.save_pair_record(pair_record)
        self.pair_record = pair_record
        return pair_record

    def start_session(
        self, pair_record: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if pair_record is None:
            pair_record = self.pair_record or self.load_saved_pair_record()
        if pair_record is None:
            raise SessionInactive("no pair record, call pair() first")
        self.pair_record = pair_record
        response = self._request(
            {
                "Request": "StartSession",
                "HostID": pair_record["HostID"],
                "SystemBUID": pair_record["SystemBUID"],
            }
        )
        self.session_id = response.get("SessionID")
        if response.get("EnableSessionSSL"):
            self._wrap_connection(pair_record)
        return response

    def stop_session(self) -> None:
        if self.session_id is None:
            return
        self._request({"Request": "StopSession", "SessionID": self.session_id})
        self.session_id = None

    # on ios 17+ the escrow bag spawns the service on a port that refuses connections
    # opt in, off by default
    def start_service(self, name: str, escrow: bool = False) -> tuple[int, bool]:
        payload: dict[str, Any] = {"Request": "StartService", "Service": name}
        if escrow and self.pair_record and self.pair_record.get("EscrowBag"):
            payload["EscrowBag"] = self.pair_record["EscrowBag"]
        response = self._request(payload)
        return response.get("Port"), bool(response.get("EnableServiceSSL"))

    def open_service(self, name: str) -> socket.socket:
        port, use_ssl = self.start_service(name)
        sock = transport.connect_device(
            self.device.device_id, port, host=self.host, mux_port=self.mux_port
        )
        if use_ssl:
            if self.pair_record is None:
                raise SessionInactive("service requires TLS but no pair record is loaded")
            sock = self._ssl_wrap(sock, self.pair_record)
        return sock

    def _wrap_connection(self, pair_record: dict[str, Any]) -> None:
        if self.connection is None or self.connection.socket is None:
            raise DeviceError("lockdown client is not connected")
        self.connection.socket = self._ssl_wrap(self.connection.socket, pair_record)

    def _ssl_wrap(
        self, sock: socket.socket, pair_record: dict[str, Any]
    ) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1
        # pair certs use rsa with weak digests, relax the security level
        # the pairing already authenticates this channel
        try:
            context.set_ciphers("DEFAULT@SECLEVEL=0")
        except ssl.SSLError:
            pass
        cert_file = self._temp_pem(pair_record["HostCertificate"])
        key_file = self._temp_pem(pair_record["HostPrivateKey"])
        context.load_cert_chain(cert_file, key_file)
        return context.wrap_socket(sock, server_hostname=None)

    def _temp_pem(self, data: bytes) -> str:
        handle, path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(handle, "wb") as out:
            out.write(bytes(data))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._ssl_temp.append(path)
        return path

    def _cleanup_ssl_temp(self) -> None:
        for path in self._ssl_temp:
            try:
                os.remove(path)
            except OSError:
                pass
        self._ssl_temp = []


def build_pair_record(
    device_public_key: Any, system_buid: str, host_id: str | None = None
) -> dict[str, Any]:
    if host_id is None:
        host_id = str(uuid.uuid4()).upper()
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    root_cert = _build_root_certificate(root_key)
    host_cert = _build_leaf_certificate(
        host_key.public_key(), root_key, root_cert, encipherment=False
    )
    device_cert = _build_leaf_certificate(
        device_public_key, root_key, root_cert, encipherment=True
    )

    pem = serialization.Encoding.PEM
    return {
        "DeviceCertificate": device_cert.public_bytes(pem),
        "HostCertificate": host_cert.public_bytes(pem),
        "RootCertificate": root_cert.public_bytes(pem),
        "HostPrivateKey": host_key.private_bytes(
            pem,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        "RootPrivateKey": root_key.private_bytes(
            pem,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        "HostID": host_id,
        "SystemBUID": system_buid,
    }


def _validity() -> tuple[datetime.datetime, datetime.datetime]:
    start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    return start, start + datetime.timedelta(days=3650)


def _build_root_certificate(root_key: Any) -> x509.Certificate:
    name = x509.Name([])
    start, end = _validity()
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(root_key.public_key())
        .serial_number(1)
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False
        )
        .sign(root_key, hashes.SHA256())
    )


def _build_leaf_certificate(
    subject_public_key: Any,
    root_key: Any,
    root_cert: x509.Certificate,
    encipherment: bool,
) -> x509.Certificate:
    start, end = _validity()
    usage = x509.KeyUsage(
        digital_signature=not encipherment,
        content_commitment=False,
        key_encipherment=encipherment,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(root_cert.subject)
        .public_key(subject_public_key)
        .serial_number(1)
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(usage, True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(subject_public_key), False
        )
        .sign(root_key, hashes.SHA256())
    )
