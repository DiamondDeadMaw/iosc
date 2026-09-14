"""Build a NIBArchive from a high level object graph."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Union

from iosc.formats import nibarchive as N

GEOMETRY_TAG = 7  # leading byte on UIBounds, UICenter and similar double runs


@dataclass
class Ref:
    target: "BuildObject"


# value that must encode as float32, not double. uikit stores most single
# precision fields this way
@dataclass
class F32:
    value: float


# a member value is a scalar, bytes, None, a Ref, or a nested BuildObject promoted
# to a Ref in flatten
Value = Union[int, float, bool, bytes, None, Ref, "BuildObject"]


@dataclass
class BuildObject:
    class_name: str
    members: list[tuple[str, Value]] = field(default_factory=list)
    class_extra_ints: list[int] = field(default_factory=list)
    _index: int = -1

    def add(self, key: str, value: Value) -> "BuildObject":
        self.members.append((key, value))
        return self


def rect(x: float, y: float, w: float, h: float) -> bytes:
    return bytes([GEOMETRY_TAG]) + struct.pack("<4d", x, y, w, h)


def point(x: float, y: float) -> bytes:
    return bytes([GEOMETRY_TAG]) + struct.pack("<2d", x, y)


def nsstring(text: str) -> BuildObject:
    return BuildObject("NSString", [("NS.bytes", text.encode("utf-8"))])


def _encode_scalar_value(key_index: int, value) -> N.NibValue:
    if isinstance(value, F32):
        return N.NibValue(key_index, N.T_FLOAT, value.value)
    if isinstance(value, bool):
        return N.NibValue(key_index, N.T_TRUE if value else N.T_FALSE, value)
    if isinstance(value, int):
        return N.NibValue(key_index, _int_type(value), value)
    if isinstance(value, float):
        return N.NibValue(key_index, N.T_DOUBLE, value)
    if isinstance(value, bytes):
        return N.NibValue(key_index, N.T_DATA, value)
    if value is None:
        return N.NibValue(key_index, N.T_NIL, None)
    raise TypeError(f"cannot encode value {value!r}")


def _int_type(value: int) -> int:
    if -128 <= value <= 127:
        return N.T_INT8
    if -32768 <= value <= 32767:
        return N.T_INT16
    if -2**31 <= value <= 2**31 - 1:
        return N.T_INT32
    return N.T_INT64


class GraphBuilder:
    def __init__(self):
        self.objects: list[BuildObject] = []

    def add(self, obj: BuildObject) -> BuildObject:
        if obj._index == -1:
            obj._index = len(self.objects)
            self.objects.append(obj)
        return obj

    def build(self, trailer: bytes = b"") -> N.NibArchive:
        # assign indices. promote nested BuildObjects seen only as a value
        for obj in list(self.objects):
            self._register_nested(obj)

        keys: list[str] = []
        key_index: dict[str, int] = {}

        def key_idx(name: str) -> int:
            if name not in key_index:
                key_index[name] = len(keys)
                keys.append(name)
            return key_index[name]

        archive = N.NibArchive(const1=1, const2=10, trailer=trailer)

        values: list[N.NibValue] = []
        for obj in self.objects:
            values_start = len(values)
            for name, value in obj.members:
                ki = key_idx(name)
                if isinstance(value, BuildObject):
                    value = Ref(value)
                if isinstance(value, Ref):
                    values.append(N.NibValue(ki, N.T_OBJECT_REF, value.target._index))
                else:
                    values.append(_encode_scalar_value(ki, value))
            archive.objects.append(
                N.NibObject(0, values_start, len(obj.members))
            )

        # class name table, deduped in first-seen order
        class_index: dict[tuple[str, tuple[int, ...]], int] = {}
        for obj, nib_obj in zip(self.objects, archive.objects):
            sig = (obj.class_name, tuple(obj.class_extra_ints))
            if sig not in class_index:
                class_index[sig] = len(archive.class_names)
                archive.class_names.append(
                    N.NibClassName(obj.class_name, list(obj.class_extra_ints))
                )
            nib_obj.class_name_index = class_index[sig]

        archive.keys = keys
        archive.values = values
        return archive

    def _register_nested(self, obj: BuildObject):
        for _, value in obj.members:
            if isinstance(value, BuildObject):
                target = value
            elif isinstance(value, Ref):
                target = value.target
            else:
                continue
            unseen = target._index == -1
            self.add(target)
            if unseen:  # guard reference cycles, e.g. a view and its constraints
                self._register_nested(target)
