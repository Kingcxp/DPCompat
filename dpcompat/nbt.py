"""Minimal typed binary NBT reader and writer for structure migration.

The codec preserves numeric tag kinds, list element types, gzip state, root name, and compound
ordering.  It is intentionally a format codec rather than a DataFixerUpper replacement.
"""

from __future__ import annotations

import gzip
import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class NbtError(ValueError):
    """Raised for malformed binary NBT or unsupported typed values."""


TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


@dataclass(slots=True)
class NbtTag:
    """One typed NBT payload; ``type_id`` is preserved during round trips."""

    type_id: int
    value: Any


@dataclass(slots=True)
class NbtList:
    """NBT list with the element type stored separately from its children."""

    element_type: int
    values: list[NbtTag]


@dataclass(slots=True)
class NbtDocument:
    """Root tag plus compression state required to rewrite one NBT file."""

    name: str
    root: NbtTag
    compressed: bool = True


class _Reader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def read_exact(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise NbtError("Unexpected end of NBT stream")
        return data

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        return struct.unpack(">" + fmt, self.read_exact(struct.calcsize(">" + fmt)))

    def string(self) -> str:
        (length,) = self.unpack("H")
        try:
            return self.read_exact(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NbtError("Invalid UTF-8 in NBT string") from exc

    def payload(self, type_id: int) -> NbtTag:
        if type_id == TAG_BYTE:
            return NbtTag(type_id, self.unpack("b")[0])
        if type_id == TAG_SHORT:
            return NbtTag(type_id, self.unpack("h")[0])
        if type_id == TAG_INT:
            return NbtTag(type_id, self.unpack("i")[0])
        if type_id == TAG_LONG:
            return NbtTag(type_id, self.unpack("q")[0])
        if type_id == TAG_FLOAT:
            return NbtTag(type_id, self.unpack("f")[0])
        if type_id == TAG_DOUBLE:
            return NbtTag(type_id, self.unpack("d")[0])
        if type_id == TAG_BYTE_ARRAY:
            (length,) = self.unpack("i")
            if length < 0:
                raise NbtError("Negative byte-array length")
            return NbtTag(type_id, self.read_exact(length))
        if type_id == TAG_STRING:
            return NbtTag(type_id, self.string())
        if type_id == TAG_LIST:
            element_type = self.unpack("b")[0]
            length = self.unpack("i")[0]
            if length < 0:
                raise NbtError("Negative list length")
            return NbtTag(
                type_id,
                NbtList(element_type, [self.payload(element_type) for _ in range(length)]),
            )
        if type_id == TAG_COMPOUND:
            result: dict[str, NbtTag] = {}
            while True:
                child_type = self.unpack("b")[0]
                if child_type == TAG_END:
                    return NbtTag(type_id, result)
                name = self.string()
                if name in result:
                    raise NbtError(f"Duplicate NBT compound key: {name!r}")
                result[name] = self.payload(child_type)
        if type_id == TAG_INT_ARRAY:
            length = self.unpack("i")[0]
            if length < 0:
                raise NbtError("Negative int-array length")
            return NbtTag(type_id, list(self.unpack(f"{length}i")) if length else [])
        if type_id == TAG_LONG_ARRAY:
            length = self.unpack("i")[0]
            if length < 0:
                raise NbtError("Negative long-array length")
            return NbtTag(type_id, list(self.unpack(f"{length}q")) if length else [])
        raise NbtError(f"Unsupported NBT tag type {type_id}")


class _Writer:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def pack(self, fmt: str, *values: Any) -> None:
        self.stream.write(struct.pack(">" + fmt, *values))

    def string(self, value: str) -> None:
        encoded = value.encode("utf-8")
        if len(encoded) > 65535:
            raise NbtError("NBT string is too long")
        self.pack("H", len(encoded))
        self.stream.write(encoded)

    def payload(self, tag: NbtTag) -> None:
        type_id, value = tag.type_id, tag.value
        if type_id == TAG_BYTE:
            self.pack("b", value)
        elif type_id == TAG_SHORT:
            self.pack("h", value)
        elif type_id == TAG_INT:
            self.pack("i", value)
        elif type_id == TAG_LONG:
            self.pack("q", value)
        elif type_id == TAG_FLOAT:
            self.pack("f", value)
        elif type_id == TAG_DOUBLE:
            self.pack("d", value)
        elif type_id == TAG_BYTE_ARRAY:
            self.pack("i", len(value))
            self.stream.write(value)
        elif type_id == TAG_STRING:
            self.string(value)
        elif type_id == TAG_LIST:
            if not isinstance(value, NbtList):
                raise NbtError("TAG_List payload must be NbtList")
            self.pack("b", value.element_type)
            self.pack("i", len(value.values))
            for item in value.values:
                if item.type_id != value.element_type:
                    raise NbtError("Heterogeneous TAG_List")
                self.payload(item)
        elif type_id == TAG_COMPOUND:
            if not isinstance(value, dict):
                raise NbtError("TAG_Compound payload must be dict")
            for name, child in value.items():
                self.pack("b", child.type_id)
                self.string(name)
                self.payload(child)
            self.pack("b", TAG_END)
        elif type_id == TAG_INT_ARRAY:
            self.pack("i", len(value))
            if value:
                self.pack(f"{len(value)}i", *value)
        elif type_id == TAG_LONG_ARRAY:
            self.pack("i", len(value))
            if value:
                self.pack(f"{len(value)}q", *value)
        else:
            raise NbtError(f"Unsupported NBT tag type {type_id}")


def loads(data: bytes) -> NbtDocument:
    """Decode gzip-compressed or raw binary NBT into a typed document."""

    compressed = data.startswith(b"\x1f\x8b")
    if compressed:
        try:
            data = gzip.decompress(data)
        except OSError as exc:
            raise NbtError("Invalid gzip-compressed NBT") from exc
    reader = _Reader(io.BytesIO(data))
    type_id = reader.unpack("b")[0]
    if type_id != TAG_COMPOUND:
        raise NbtError("Root NBT tag must be a compound")
    name = reader.string()
    root = reader.payload(type_id)
    if reader.stream.read(1):
        raise NbtError("Unexpected trailing bytes after root NBT compound")
    return NbtDocument(name, root, compressed)


def dumps(document: NbtDocument) -> bytes:
    """Encode a typed document, preserving its original compression mode."""

    buffer = io.BytesIO()
    writer = _Writer(buffer)
    writer.pack("b", document.root.type_id)
    writer.string(document.name)
    writer.payload(document.root)
    data = buffer.getvalue()
    return gzip.compress(data, mtime=0) if document.compressed else data


def load_path(path: Path) -> NbtDocument:
    """Read and decode one binary NBT file."""

    return loads(path.read_bytes())


def dump_path(path: Path, document: NbtDocument) -> None:
    """Encode and overwrite one binary NBT file."""

    path.write_bytes(dumps(document))


def compound(tag: NbtTag) -> dict[str, NbtTag] | None:
    """Return a compound payload when ``tag`` has compound type."""

    return tag.value if tag.type_id == TAG_COMPOUND and isinstance(tag.value, dict) else None


def list_values(tag: NbtTag, element_type: int | None = None) -> list[NbtTag] | None:
    """Return list children, optionally requiring a specific element type."""

    if tag.type_id != TAG_LIST or not isinstance(tag.value, NbtList):
        return None
    if element_type is not None and tag.value.element_type != element_type:
        return None
    return tag.value.values
