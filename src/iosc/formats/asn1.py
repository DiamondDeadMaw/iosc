import datetime
from typing import Any

from iosc.core.errors import Asn1Error

BOOLEAN = 0x01
INTEGER = 0x02
BIT_STRING = 0x03
OCTET_STRING = 0x04
NULL = 0x05
OBJECT_IDENTIFIER = 0x06
UTF8_STRING = 0x0C
PRINTABLE_STRING = 0x13
T61_STRING = 0x14
IA5_STRING = 0x16
UTC_TIME = 0x17
GENERALIZED_TIME = 0x18
SEQUENCE = 0x30
SET = 0x31

CONSTRUCTED = 0x20
CONTEXT = 0x80

STRING_TAGS = (UTF8_STRING, PRINTABLE_STRING, T61_STRING, IA5_STRING)


def context(number: int, constructed: bool = True) -> int:
    return CONTEXT | (CONSTRUCTED if constructed else 0) | number


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def encode(tag: int, payload: bytes) -> bytes:
    if tag > 0xFF:
        raise Asn1Error(f"multi-byte tag {tag:#x} is not supported")
    return bytes([tag]) + encode_length(len(payload)) + payload


def encode_boolean(value: bool) -> bytes:
    return encode(BOOLEAN, b"\xff" if value else b"\x00")


def encode_integer(value: int) -> bytes:
    length = max(1, (value.bit_length() + 8) // 8)
    return encode(INTEGER, value.to_bytes(length, "big", signed=True))


def encode_oid(dotted: str) -> bytes:
    parts = [int(part) for part in dotted.split(".")]
    if len(parts) < 2:
        raise Asn1Error(f"object identifier {dotted!r} needs at least two arcs")
    body = bytearray([parts[0] * 40 + parts[1]])
    for arc in parts[2:]:
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.insert(0, (arc & 0x7F) | 0x80)
            arc >>= 7
        body += chunk
    return encode(OBJECT_IDENTIFIER, bytes(body))


def encode_null() -> bytes:
    return encode(NULL, b"")


def encode_octet_string(payload: bytes) -> bytes:
    return encode(OCTET_STRING, payload)


def encode_bit_string(payload: bytes, unused_bits: int = 0) -> bytes:
    return encode(BIT_STRING, bytes([unused_bits]) + payload)


def encode_utf8(text: str) -> bytes:
    return encode(UTF8_STRING, text.encode("utf-8"))


def encode_printable(text: str) -> bytes:
    return encode(PRINTABLE_STRING, text.encode("ascii"))


def encode_ia5(text: str) -> bytes:
    return encode(IA5_STRING, text.encode("ascii"))


def encode_utc_time(when: datetime.datetime) -> bytes:
    if not 1950 <= when.year < 2050:
        raise Asn1Error(f"year {when.year} does not fit UTCTime")
    return encode(UTC_TIME, when.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def encode_sequence(*parts: bytes) -> bytes:
    return encode(SEQUENCE, b"".join(parts))


def encode_set(*parts: bytes) -> bytes:
    return encode(SET, b"".join(parts))


def encode_set_of(parts: Any) -> bytes:
    return encode(SET, b"".join(sorted(parts)))


def encode_explicit(number: int, payload: bytes) -> bytes:
    return encode(context(number), payload)


def encode_implicit(number: int, encoded: bytes) -> bytes:
    node, end = parse(encoded)
    if end != len(encoded):
        raise Asn1Error("implicit tagging expects exactly one value")
    constructed = CONSTRUCTED if node.constructed else 0
    return encode(CONTEXT | constructed | number, node.value)


class Node:
    __slots__ = ("tag", "value", "start", "end", "raw", "indefinite")

    def __init__(
        self,
        tag: int,
        value: bytes,
        start: int,
        end: int,
        raw: bytes,
        indefinite: bool = False,
    ):
        self.tag = tag
        self.value = value
        self.start = start
        self.end = end
        self.raw = raw
        self.indefinite = indefinite

    @property
    def constructed(self) -> bool:
        return bool(self.tag & CONSTRUCTED)

    @property
    def context_number(self) -> int | None:
        return self.tag & 0x1F if self.tag & CONTEXT else None

    @property
    def children(self) -> list["Node"]:
        if not self.constructed:
            raise Asn1Error(f"tag {self.tag:#x} is primitive and has no children")
        return parse_all(self.value)

    def child(self, index: int) -> "Node":
        return self.children[index]

    def as_int(self) -> int:
        if self.tag != INTEGER:
            raise Asn1Error(f"expected INTEGER, found tag {self.tag:#x}")
        return int.from_bytes(self.value, "big", signed=True)

    def as_bool(self) -> bool:
        if self.tag != BOOLEAN:
            raise Asn1Error(f"expected BOOLEAN, found tag {self.tag:#x}")
        return self.value != b"\x00"

    def as_oid(self) -> str:
        if self.tag != OBJECT_IDENTIFIER:
            raise Asn1Error(f"expected OBJECT IDENTIFIER, found tag {self.tag:#x}")
        if not self.value:
            raise Asn1Error("object identifier is empty")
        arcs = [self.value[0] // 40, self.value[0] % 40]
        arc = 0
        for byte in self.value[1:]:
            arc = (arc << 7) | (byte & 0x7F)
            if not byte & 0x80:
                arcs.append(arc)
                arc = 0
        return ".".join(str(a) for a in arcs)

    def as_text(self) -> str:
        if self.tag not in STRING_TAGS:
            raise Asn1Error(f"tag {self.tag:#x} is not a string type")
        return self.value.decode("utf-8", "replace")

    def as_bit_string(self) -> bytes:
        if self.tag != BIT_STRING:
            raise Asn1Error(f"expected BIT STRING, found tag {self.tag:#x}")
        if self.value[0]:
            raise Asn1Error(f"bit string has {self.value[0]} unused bits")
        return self.value[1:]

    def __repr__(self) -> str:
        return f"<Node tag={self.tag:#04x} len={len(self.value)}>"


def _indefinite_end(data: bytes, body: int) -> int:
    offset = body
    while True:
        if offset + 2 > len(data):
            raise Asn1Error(f"indefinite value at {body} is missing its end-of-contents")
        if data[offset] == 0x00 and data[offset + 1] == 0x00:
            return offset
        _, offset = parse(data, offset)


def parse(data: bytes, offset: int = 0) -> tuple[Node, int]:
    if offset + 2 > len(data):
        raise Asn1Error(f"truncated value at offset {offset}")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise Asn1Error("multi-byte tags are not supported")
    first = data[offset + 1]
    if first == 0x80:
        if not tag & CONSTRUCTED:
            raise Asn1Error(f"primitive tag {tag:#x} cannot use indefinite length")
        body = offset + 2
        content_end = _indefinite_end(data, body)
        return (
            Node(
                tag,
                data[body:content_end],
                offset,
                content_end + 2,
                data[offset : content_end + 2],
                indefinite=True,
            ),
            content_end + 2,
        )
    if first < 0x80:
        length = first
        body = offset + 2
    else:
        count = first & 0x7F
        if offset + 2 + count > len(data):
            raise Asn1Error(f"truncated length at offset {offset}")
        length = int.from_bytes(data[offset + 2 : offset + 2 + count], "big")
        if length < 0x80:
            raise Asn1Error(f"length {length} is not in minimal form")
        body = offset + 2 + count
    end = body + length
    if end > len(data):
        raise Asn1Error(f"value at {offset} claims {length} bytes, {len(data) - body} remain")
    return Node(tag, data[body:end], offset, end, data[offset:end]), end


def parse_all(data: bytes) -> list[Node]:
    nodes = []
    offset = 0
    while offset < len(data):
        if data[offset] == 0x00 and offset + 1 < len(data) and data[offset + 1] == 0x00:
            break
        node, offset = parse(data, offset)
        nodes.append(node)
    return nodes


def parse_one(data: bytes) -> Node:
    node, end = parse(data)
    if end != len(data):
        raise Asn1Error(f"{len(data) - end} trailing bytes after the value")
    return node


def find_oid(node: Node, oid: str) -> list[Node]:
    found = []
    if node.constructed:
        children = node.children
        if children and children[0].tag == OBJECT_IDENTIFIER:
            try:
                if children[0].as_oid() == oid:
                    found.append(node)
            except Asn1Error:
                pass
        for child in children:
            found.extend(find_oid(child, oid))
    return found
