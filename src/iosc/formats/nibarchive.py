"""NIBArchive binary codec.

Four parallel tables (objects, keys, values, class names) behind a fixed header.
Parse then re-emit must be byte identical. Reference is matsmattsson/nibsqueeze
NibArchive.md, cross checked against real nibs in testdata/nib_corpus.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from iosc.core.errors import FormatError

MAGIC = b"NIBArchive"
HEADER_SIZE = 50

# value type tags, one per nscoder primitive
T_INT8 = 0
T_INT16 = 1
T_INT32 = 2
T_INT64 = 3
T_TRUE = 4
T_FALSE = 5
T_FLOAT = 6
T_DOUBLE = 7
T_DATA = 8
T_NIL = 9
T_OBJECT_REF = 10


class NibFormatError(FormatError):
    pass


@dataclass
class NibObject:
    class_name_index: int
    values_index: int
    value_count: int


@dataclass
class NibValue:
    key_index: int
    type: int
    payload: Any  # int, float, bytes, None, or object index for T_OBJECT_REF


@dataclass
class NibClassName:
    name: str
    extra_ints: list[int] = field(default_factory=list)
    terminator: int = 1  # trailing null bytes counted in the on-disk length


@dataclass
class NibArchive:
    const1: int = 1
    const2: int = 10
    objects: list[NibObject] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    values: list[NibValue] = field(default_factory=list)
    class_names: list[NibClassName] = field(default_factory=list)
    trailer: bytes = b""  # opaque bytes some nibs carry after the four sections


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def seek(self, pos: int):
        self.pos = pos

    def u8(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def varint(self) -> int:
        result = 0
        shift = 0
        while True:
            b = self.data[self.pos]
            self.pos += 1
            result |= (b & 0x7F) << shift
            if b & 0x80:
                return result
            shift += 7

    def take(self, n: int) -> bytes:
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise NibFormatError(f"varint cannot encode negative {value}")
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value == 0:
            out.append(chunk | 0x80)
            return bytes(out)
        out.append(chunk)


def parse(data: bytes) -> NibArchive:
    if data[:10] != MAGIC:
        raise NibFormatError("not a NIBArchive, missing magic")
    r = _Reader(data)
    r.seek(10)
    const1 = r.u32()
    const2 = r.u32()
    object_count = r.u32()
    objects_offset = r.u32()
    key_count = r.u32()
    keys_offset = r.u32()
    value_count = r.u32()
    values_offset = r.u32()
    class_name_count = r.u32()
    class_names_offset = r.u32()

    archive = NibArchive(const1=const1, const2=const2)

    r.seek(objects_offset)
    for _ in range(object_count):
        archive.objects.append(NibObject(r.varint(), r.varint(), r.varint()))

    r.seek(keys_offset)
    for _ in range(key_count):
        n = r.varint()
        archive.keys.append(r.take(n).decode("utf-8"))

    r.seek(values_offset)
    for _ in range(value_count):
        key_index = r.varint()
        vtype = r.u8()
        payload = _read_value_payload(r, vtype)
        archive.values.append(NibValue(key_index, vtype, payload))

    r.seek(class_names_offset)
    for _ in range(class_name_count):
        length = r.varint()
        extra_count = r.varint()
        extra = [struct.unpack_from("<i", r.take(4))[0] for _ in range(extra_count)]
        raw_name = r.take(length)
        stripped = raw_name.rstrip(b"\x00")
        terminator = length - len(stripped)
        name = stripped.decode("utf-8")
        archive.class_names.append(NibClassName(name, extra, terminator))

    # class names are the last section. anything past here is an opaque trailer
    archive.trailer = data[r.pos:]
    return archive


def _read_value_payload(r: _Reader, vtype: int) -> Any:
    if vtype == T_INT8:
        return struct.unpack_from("<b", r.take(1))[0]
    if vtype == T_INT16:
        return struct.unpack_from("<h", r.take(2))[0]
    if vtype == T_INT32:
        return struct.unpack_from("<i", r.take(4))[0]
    if vtype == T_INT64:
        return struct.unpack_from("<q", r.take(8))[0]
    if vtype == T_TRUE:
        return True
    if vtype == T_FALSE:
        return False
    if vtype == T_FLOAT:
        return struct.unpack_from("<f", r.take(4))[0]
    if vtype == T_DOUBLE:
        return struct.unpack_from("<d", r.take(8))[0]
    if vtype == T_DATA:
        n = r.varint()
        return r.take(n)
    if vtype == T_NIL:
        return None
    if vtype == T_OBJECT_REF:
        return r.u32()
    raise NibFormatError(f"unknown value type {vtype}")


def _write_value_payload(out: bytearray, value: NibValue):
    t = value.type
    p = value.payload
    if t == T_INT8:
        out += struct.pack("<b", p)
    elif t == T_INT16:
        out += struct.pack("<h", p)
    elif t == T_INT32:
        out += struct.pack("<i", p)
    elif t == T_INT64:
        out += struct.pack("<q", p)
    elif t in (T_TRUE, T_FALSE, T_NIL):
        pass
    elif t == T_FLOAT:
        out += struct.pack("<f", p)
    elif t == T_DOUBLE:
        out += struct.pack("<d", p)
    elif t == T_DATA:
        out += _encode_varint(len(p))
        out += p
    elif t == T_OBJECT_REF:
        out += struct.pack("<I", p)
    else:
        raise NibFormatError(f"unknown value type {t}")


def serialize(archive: NibArchive) -> bytes:
    objects_buf = bytearray()
    for obj in archive.objects:
        objects_buf += _encode_varint(obj.class_name_index)
        objects_buf += _encode_varint(obj.values_index)
        objects_buf += _encode_varint(obj.value_count)

    keys_buf = bytearray()
    for key in archive.keys:
        kb = key.encode("utf-8")
        keys_buf += _encode_varint(len(kb))
        keys_buf += kb

    values_buf = bytearray()
    for value in archive.values:
        values_buf += _encode_varint(value.key_index)
        values_buf.append(value.type)
        _write_value_payload(values_buf, value)

    class_names_buf = bytearray()
    for cn in archive.class_names:
        nb = cn.name.encode("utf-8")
        class_names_buf += _encode_varint(len(nb) + cn.terminator)
        class_names_buf += _encode_varint(len(cn.extra_ints))
        for extra in cn.extra_ints:
            class_names_buf += struct.pack("<i", extra)
        class_names_buf += nb
        class_names_buf += b"\x00" * cn.terminator

    objects_offset = HEADER_SIZE
    keys_offset = objects_offset + len(objects_buf)
    values_offset = keys_offset + len(keys_buf)
    class_names_offset = values_offset + len(values_buf)

    header = bytearray()
    header += MAGIC
    header += struct.pack("<I", archive.const1)
    header += struct.pack("<I", archive.const2)
    header += struct.pack("<I", len(archive.objects))
    header += struct.pack("<I", objects_offset)
    header += struct.pack("<I", len(archive.keys))
    header += struct.pack("<I", keys_offset)
    header += struct.pack("<I", len(archive.values))
    header += struct.pack("<I", values_offset)
    header += struct.pack("<I", len(archive.class_names))
    header += struct.pack("<I", class_names_offset)

    return bytes(
        header + objects_buf + keys_buf + values_buf + class_names_buf
        + archive.trailer
    )
