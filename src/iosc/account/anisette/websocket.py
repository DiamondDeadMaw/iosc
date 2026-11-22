import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from typing import Any
from urllib.parse import urlsplit

from iosc.core import AnisetteError

ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
MAX_HEAD = 16384


def accept_key(key: str) -> str:
    digest = hashlib.sha1((key + ACCEPT_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes, mask: bytes | None = None) -> bytes:
    mask = mask if mask is not None else os.urandom(4)
    size = len(payload)
    if size < 126:
        head = struct.pack(">BB", 0x80 | opcode, 0x80 | size)
    elif size < 1 << 16:
        head = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, size)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, size)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


class WebSocket:
    def __init__(self, sock: Any) -> None:
        self.sock = sock
        self._buffer = b""

    @classmethod
    def connect(
        cls, url: str, timeout: float = 20, context: ssl.SSLContext | None = None
    ) -> "WebSocket":
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise AnisetteError(f"{url!r} is not a websocket url")
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        host = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"

        try:
            sock = socket.create_connection((parts.hostname, port), timeout=timeout)
        except OSError as exc:
            raise AnisetteError(f"could not reach {url}: {exc}") from exc
        try:
            if secure:
                ctx = context or ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=parts.hostname)
            ws = cls(sock)
            ws._handshake(host, path)
        except BaseException:
            sock.close()
            raise
        return ws

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        head = self._read_until(b"\r\n\r\n").decode("latin-1")
        lines = head.split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise AnisetteError(f"websocket upgrade refused: {lines[0]}")
        fields = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            fields[name.strip().lower()] = value.strip()
        if fields.get("sec-websocket-accept") != accept_key(key):
            raise AnisetteError("websocket upgrade answered with a wrong accept key")

    def _fill(self) -> None:
        try:
            chunk = self.sock.recv(65536)
        except OSError as exc:
            raise AnisetteError(f"websocket read failed: {exc}") from exc
        if not chunk:
            raise AnisetteError("websocket closed by the server")
        self._buffer += chunk

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) > MAX_HEAD:
                raise AnisetteError("websocket upgrade response too large")
            self._fill()
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            self._fill()
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        size = second & 0x7F
        if size == 126:
            (size,) = struct.unpack(">H", self._read_exact(2))
        elif size == 127:
            (size,) = struct.unpack(">Q", self._read_exact(8))
        mask = self._read_exact(4) if second & 0x80 else None
        payload = self._read_exact(size)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(OP_TEXT, text.encode("utf-8")))

    def recv_text(self) -> str:
        parts: list[bytes] = []
        while True:
            final, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self.sock.sendall(encode_frame(OP_PONG, payload))
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                reason = payload[2:].decode("utf-8", errors="replace")
                raise AnisetteError(f"websocket closed by the server ({code} {reason})".strip())
            if opcode in (OP_TEXT, OP_BINARY, OP_CONTINUATION):
                parts.append(payload)
                if final:
                    return b"".join(parts).decode("utf-8")

    def send_json(self, value: Any) -> None:
        self.send_text(json.dumps(value))

    def recv_json(self) -> dict[str, Any]:
        text = self.recv_text()
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise AnisetteError(f"websocket message is not json: {text[:200]}") from exc
        if not isinstance(value, dict):
            raise AnisetteError(f"websocket message is not an object: {text[:200]}")
        return value

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(OP_CLOSE, struct.pack(">H", 1000)))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
