import plistlib
import socket
import struct
from dataclasses import dataclass, field
from typing import Any

from iosc.core.errors import DeviceError

# Apple Mobile Device Service, installed with iTunes or the Apple Devices app
USBMUX_HOST = "127.0.0.1"
USBMUX_PORT = 27015

# Little endian length, version, message, tag
HEADER = struct.Struct("<IIII")
HEADER_SIZE = 16
VERSION_PLIST = 1
MESSAGE_PLIST = 8

CLIENT_VERSION = "iosc-usbmux-1.0"
PROG_NAME = "iosc"
LIBUSBMUX_VERSION = 3

RESULT_OK = 0
RESULT_BADCOMMAND = 1
RESULT_BADDEV = 2
RESULT_CONNREFUSED = 3

RESULT_NAMES = {
    RESULT_OK: "ok",
    RESULT_BADCOMMAND: "bad command",
    RESULT_BADDEV: "unknown device",
    RESULT_CONNREFUSED: "connection refused by device",
}

# big endian length then an xml plist, lockdown and installation proxy
FRAME_LENGTH = struct.Struct(">I")


class UsbmuxProtocolError(DeviceError):
    def __init__(self, message: str, number: int | None = None) -> None:
        super().__init__(message)
        self.number = number


@dataclass(frozen=True)
class DeviceInfo:
    device_id: int
    serial: str
    connection_type: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def udid(self) -> str:
        return self.serial


def recv_exact(sock: socket.socket, count: int, what: str = "connection") -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise DeviceError(f"{what} closed while reading")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# length prefixed plist framing shared by every lockdown style service
class PlistConnection:
    def __init__(self, sock: socket.socket, what: str = "service") -> None:
        self.socket: socket.socket | None = sock
        self.what = what

    def send(self, payload: dict[str, Any]) -> None:
        if self.socket is None:
            raise DeviceError(f"{self.what} connection is closed")
        body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
        self.socket.sendall(FRAME_LENGTH.pack(len(body)) + body)

    def receive(self) -> dict[str, Any]:
        if self.socket is None:
            raise DeviceError(f"{self.what} connection is closed")
        (length,) = FRAME_LENGTH.unpack(recv_exact(self.socket, 4, self.what))
        return plistlib.loads(recv_exact(self.socket, length, self.what))

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def __enter__(self) -> "PlistConnection":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class UsbmuxClient:
    def __init__(
        self,
        host: str = USBMUX_HOST,
        port: int = USBMUX_PORT,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self._tag = 0

    def connect(self) -> "UsbmuxClient":
        try:
            self.socket = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as error:
            raise DeviceError(
                f"cannot reach Apple Mobile Device Service at {self.host}:{self.port}. "
                f"Install iTunes or the Apple Devices app and make sure it is running "
                f"({error})"
            ) from error
        return self

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def __enter__(self) -> "UsbmuxClient":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _next_tag(self) -> int:
        self._tag += 1
        return self._tag

    def _send(self, payload: dict[str, Any]) -> int:
        if self.socket is None:
            raise DeviceError("usbmux client is not connected")
        body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
        tag = self._next_tag()
        header = HEADER.pack(
            HEADER_SIZE + len(body), VERSION_PLIST, MESSAGE_PLIST, tag
        )
        self.socket.sendall(header + body)
        return tag

    def _recv(self) -> dict[str, Any]:
        if self.socket is None:
            raise DeviceError("usbmux client is not connected")
        header = recv_exact(self.socket, HEADER_SIZE, "usbmux")
        length, _version, message, _tag = HEADER.unpack(header)
        body = recv_exact(self.socket, length - HEADER_SIZE, "usbmux")
        if message != MESSAGE_PLIST:
            raise UsbmuxProtocolError(f"unexpected usbmux message type {message}")
        return plistlib.loads(body)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload.setdefault("ClientVersionString", CLIENT_VERSION)
        payload.setdefault("ProgName", PROG_NAME)
        payload.setdefault("kLibUSBMuxVersion", LIBUSBMUX_VERSION)
        self._send(payload)
        return self._recv()

    def _check_result(self, response: dict[str, Any]) -> None:
        number = response.get("Number")
        if number != RESULT_OK:
            raise UsbmuxProtocolError(
                RESULT_NAMES.get(number, f"usbmux error {number}"), number
            )

    def list_devices(self) -> list[DeviceInfo]:
        response = self._request({"MessageType": "ListDevices"})
        devices: list[DeviceInfo] = []
        for entry in response.get("DeviceList", []):
            props = entry.get("Properties", {})
            devices.append(
                DeviceInfo(
                    device_id=entry.get("DeviceID"),
                    serial=props.get("SerialNumber", ""),
                    connection_type=props.get("ConnectionType", ""),
                    properties=props,
                )
            )
        return devices

    # afc works better on usb. wifi duplicates the device
    def find_device(self, udid: str | None = None) -> DeviceInfo | None:
        devices = self.list_devices()
        if udid is not None:
            devices = [d for d in devices if d.serial == udid]
        if not devices:
            return None
        usb = [d for d in devices if d.connection_type == "USB"]
        return (usb or devices)[0]

    def read_buid(self) -> str:
        return self._request({"MessageType": "ReadBUID"}).get("BUID", "")

    def read_pair_record(self, udid: str) -> dict[str, Any] | None:
        response = self._request(
            {"MessageType": "ReadPairRecord", "PairRecordID": udid}
        )
        data = response.get("PairRecordData")
        if not data:
            return None
        return plistlib.loads(bytes(data))

    def save_pair_record(
        self,
        udid: str,
        record: dict[str, Any],
        device_id: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "MessageType": "SavePairRecord",
            "PairRecordID": udid,
            "PairRecordData": plistlib.dumps(record, fmt=plistlib.FMT_XML),
        }
        if device_id is not None:
            payload["DeviceID"] = device_id
        self._request(payload)

    # PortNumber travels in network byte order although the header is little endian
    def connect_device(self, device_id: int, port: int) -> socket.socket:
        network_port = struct.unpack("<H", struct.pack(">H", port))[0]
        response = self._request(
            {
                "MessageType": "Connect",
                "DeviceID": device_id,
                "PortNumber": network_port,
            }
        )
        self._check_result(response)
        tunnel = self.socket
        if tunnel is None:
            raise DeviceError("usbmux tunnel disappeared during connect")
        # socket is a raw tunnel now. detach it from this client
        self.socket = None
        return tunnel


def list_devices(
    host: str = USBMUX_HOST, port: int = USBMUX_PORT
) -> list[DeviceInfo]:
    with UsbmuxClient(host, port) as client:
        return client.list_devices()


def connect_device(
    device_id: int,
    port: int,
    host: str = USBMUX_HOST,
    mux_port: int = USBMUX_PORT,
) -> socket.socket:
    client = UsbmuxClient(host, mux_port).connect()
    return client.connect_device(device_id, port)
