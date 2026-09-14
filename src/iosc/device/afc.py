import socket
import struct
from pathlib import Path

from iosc.core.errors import DeviceError
from iosc.device.transport import recv_exact

AFC_SERVICE = "com.apple.afc"

MAGIC = b"CFA6LPAA"
# entire_length, this_length, packet_num, operation
HEADER = struct.Struct("<QQQQ")
HEADER_SIZE = 8 + HEADER.size

OP_STATUS = 0x00000001
OP_DATA = 0x00000002
OP_READ_DIR = 0x00000003
OP_REMOVE_PATH = 0x00000008
OP_MAKE_DIR = 0x00000009
OP_GET_FILE_INFO = 0x0000000A
OP_FILE_OPEN = 0x0000000D
OP_FILE_OPEN_RES = 0x0000000E
OP_FILE_WRITE = 0x00000010
OP_FILE_CLOSE = 0x00000014

FOPEN_WRONLY = 0x00000003

STATUS_SUCCESS = 0
STATUS_OBJECT_EXISTS = 12

STATUS_NAMES = {
    1: "unknown error",
    2: "operation header invalid",
    4: "no resources",
    7: "object not found",
    8: "object is directory",
    9: "permission denied",
    10: "not connected",
    12: "object exists",
    14: "no space left",
}


class AfcStatusError(DeviceError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _status_error(data: bytes) -> AfcStatusError:
    (status,) = struct.unpack("<Q", data[:8])
    return AfcStatusError(STATUS_NAMES.get(status, f"AFC error {status}"), status)


def _cstr(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def _parse_pairs(data: bytes) -> dict[str, str]:
    tokens = [t.decode("utf-8") for t in data.split(b"\x00") if t]
    return dict(zip(tokens[0::2], tokens[1::2]))


class AfcClient:
    def __init__(self, sock: socket.socket) -> None:
        self.socket: socket.socket | None = sock
        self._packet_num = 0

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def __enter__(self) -> "AfcClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _dispatch(
        self, operation: int, header_data: bytes = b"", payload: bytes = b""
    ) -> tuple[int, bytes]:
        if self.socket is None:
            raise DeviceError("AFC connection is closed")
        this_length = HEADER_SIZE + len(header_data)
        entire_length = this_length + len(payload)
        packet = (
            MAGIC
            + HEADER.pack(entire_length, this_length, self._packet_num, operation)
            + header_data
            + payload
        )
        self.socket.sendall(packet)
        self._packet_num += 1
        return self._recv()

    def _recv(self) -> tuple[int, bytes]:
        if self.socket is None:
            raise DeviceError("AFC connection is closed")
        head = recv_exact(self.socket, HEADER_SIZE, "AFC")
        if head[:8] != MAGIC:
            raise DeviceError("bad AFC packet magic")
        entire_length, _this_length, _num, operation = HEADER.unpack(head[8:])
        rest = recv_exact(self.socket, entire_length - HEADER_SIZE, "AFC")
        return operation, rest

    def _dispatch_checked(
        self, operation: int, header_data: bytes = b"", payload: bytes = b""
    ) -> tuple[int, bytes]:
        op, data = self._dispatch(operation, header_data, payload)
        if op == OP_STATUS:
            (status,) = struct.unpack("<Q", data[:8])
            if status != STATUS_SUCCESS:
                raise _status_error(data)
        return op, data

    def make_dir(self, path: str) -> None:
        self._dispatch_checked(OP_MAKE_DIR, _cstr(path))

    def remove(self, path: str) -> None:
        self._dispatch_checked(OP_REMOVE_PATH, _cstr(path))

    def get_file_info(self, path: str) -> dict[str, str]:
        op, data = self._dispatch(OP_GET_FILE_INFO, _cstr(path))
        if op == OP_STATUS:
            raise _status_error(data)
        return _parse_pairs(data)

    def read_dir(self, path: str) -> list[str]:
        op, data = self._dispatch(OP_READ_DIR, _cstr(path))
        if op == OP_STATUS:
            raise _status_error(data)
        return [
            n.decode("utf-8")
            for n in data.split(b"\x00")
            if n and n not in (b".", b"..")
        ]

    def file_open(self, path: str, mode: int = FOPEN_WRONLY) -> int:
        header = struct.pack("<Q", mode) + _cstr(path)
        op, data = self._dispatch(OP_FILE_OPEN, header)
        if op != OP_FILE_OPEN_RES:
            raise _status_error(data)
        (handle,) = struct.unpack("<Q", data[:8])
        return handle

    def file_write(self, handle: int, data: bytes, chunk: int = 1 << 16) -> None:
        view = memoryview(data)
        offset = 0
        header_prefix = struct.pack("<Q", handle)
        while offset < len(view):
            piece = view[offset : offset + chunk]
            self._dispatch_checked(OP_FILE_WRITE, header_prefix, bytes(piece))
            offset += len(piece)

    def file_close(self, handle: int) -> None:
        self._dispatch_checked(OP_FILE_CLOSE, struct.pack("<Q", handle))

    def write_file(self, path: str, data: bytes) -> None:
        handle = self.file_open(path, FOPEN_WRONLY)
        try:
            self.file_write(handle, data)
        finally:
            self.file_close(handle)

    def make_dir_p(self, path: str) -> None:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        current = ""
        for part in parts:
            current = current + "/" + part
            try:
                self.make_dir(current)
            except AfcStatusError as error:
                if error.status != STATUS_OBJECT_EXISTS:
                    raise

    def upload_path(self, local_path: Path | str, remote_path: str) -> None:
        local = Path(local_path)
        remote_path = remote_path.replace("\\", "/")
        if local.is_dir():
            self.make_dir_p(remote_path)
            for child in sorted(local.iterdir(), key=lambda p: p.name):
                self.upload_path(child, remote_path + "/" + child.name)
        else:
            self.write_file(remote_path, local.read_bytes())
