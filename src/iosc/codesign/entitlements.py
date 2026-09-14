from typing import Any

from iosc.core.errors import SigningError
from iosc.formats.plist import write_plist
from iosc.codesign.directory import (
    CSMAGIC_EMBEDDED_DER_ENTITLEMENTS,
    CSMAGIC_EMBEDDED_ENTITLEMENTS,
    blob,
)


def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _der(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(payload)) + payload


def _der_value(value: Any) -> bytes:
    if isinstance(value, bool):
        return _der(0x01, b"\xff" if value else b"\x00")
    if isinstance(value, int):
        length = max(1, (value.bit_length() + 8) // 8)
        return _der(0x02, value.to_bytes(length, "big", signed=True))
    if isinstance(value, str):
        return _der(0x0C, value.encode("utf-8"))
    if isinstance(value, (list, tuple)):
        return _der(0x30, b"".join(_der_value(v) for v in value))
    if isinstance(value, dict):
        return _der(
            0x31,
            b"".join(
                _der(0x30, _der(0x0C, k.encode("utf-8")) + _der_value(v))
                for k, v in sorted(value.items())
            ),
        )
    raise SigningError(f"cannot DER encode {type(value).__name__}")


def encode_der_entitlements(entitlements: dict[str, Any]) -> bytes:
    pairs = b"".join(
        _der(0x30, _der(0x0C, key.encode("utf-8")) + _der_value(value))
        for key, value in sorted(entitlements.items())
    )
    return _der(0x70, _der(0x02, b"\x01") + _der(0xB0, pairs))


def build_entitlements_blobs(entitlements: dict[str, Any]) -> tuple[bytes, bytes]:
    xml = write_plist(entitlements, binary=False)
    der = encode_der_entitlements(entitlements)
    return (
        blob(CSMAGIC_EMBEDDED_ENTITLEMENTS, xml),
        blob(CSMAGIC_EMBEDDED_DER_ENTITLEMENTS, der),
    )
