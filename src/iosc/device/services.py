import socket
from typing import Iterator

SYSLOG_SERVICE = "com.apple.syslog_relay"


# relay writes nul-padded lines. split on newlines and strip padding
def stream_syslog(sock: socket.socket, contains: str | None = None) -> Iterator[str]:
    buffer = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace").rstrip("\x00")
            if contains is None or contains in text:
                yield text
