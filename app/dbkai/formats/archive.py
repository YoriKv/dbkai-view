"""``archiveDBK.dsa``: the game's own pack of most of its assets.

A ``'DSA '`` header, a directory table, an entry table, then every entry's
bytes: its name (NUL-padded to the length the table gives) immediately followed
by its data, which is a BIOS LZ77 stream when the packed and unpacked sizes
differ and stored raw when they match. See ``docs/formats/archive.md``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import cached_property

from dbkai.formats.compression import lz77_decompress

MAGIC = b"DSA "
_HEADER = struct.Struct("<4sIII")
_DIR = struct.Struct("<III")
_ENTRY = struct.Struct("<IIIII")


class ArchiveError(ValueError):
    """The bytes are not an archive this reader understands."""


@dataclass(frozen=True)
class ArchiveEntry:
    """One packed file. ``directory`` is the table's path (``/mdl/chr``) and
    ``name`` the file name stored in front of the data; an entry with no
    stored name is named after its id."""

    index: int
    directory: str
    name: str
    entry_id: int
    unpacked_size: int
    packed_size: int
    offset: int
    name_length: int

    @property
    def path(self) -> str:
        return f"{self.directory}/{self.name}"

    @property
    def compressed(self) -> bool:
        return self.packed_size != self.unpacked_size

    @property
    def data_offset(self) -> int:
        return self.offset + self.name_length


class DsaArchive:
    """The archive, parsed lazily: entries are listed up front, and each one's
    bytes are unpacked on request."""

    def __init__(self, data: bytes) -> None:
        if len(data) < _HEADER.size or data[:4] != MAGIC:
            raise ArchiveError("not a 'DSA ' archive")
        magic, version, self.data_start, dir_count = _HEADER.unpack_from(data)
        if version != 1:
            raise ArchiveError(f"unsupported archive version {version}")
        self.data = data
        dirs = [
            _DIR.unpack_from(data, _HEADER.size + _DIR.size * i)
            for i in range(dir_count)
        ]
        table = _HEADER.size + _DIR.size * dir_count
        # The directory names sit right after the entry table, so the first
        # name offset is where the entries stop.
        strings = min(name for _, _, name in dirs) if dirs else table
        entries: list[ArchiveEntry] = []
        for first, count, name_offset in dirs:
            directory = _cstring(data, name_offset)
            for index in range(first, first + count):
                pos = table + _ENTRY.size * index
                if pos + _ENTRY.size > strings:
                    raise ArchiveError(
                        f"directory {directory} lists entries past the table"
                    )
                name_length, entry_id, unpacked, packed, offset = _ENTRY.unpack_from(
                    data, pos
                )
                name = (
                    data[offset : offset + name_length]
                    .rstrip(b"\0")
                    .decode("ascii", "replace")
                )
                entries.append(
                    ArchiveEntry(
                        index=index,
                        directory=directory,
                        name=name or f"{entry_id:08x}.bin",
                        entry_id=entry_id,
                        unpacked_size=unpacked,
                        packed_size=packed,
                        offset=offset,
                        name_length=name_length,
                    )
                )
        entries.sort(key=lambda e: e.index)
        self.entries = entries

    @cached_property
    def _by_path(self) -> dict[str, ArchiveEntry]:
        return {e.path: e for e in self.entries}

    def find(self, path: str) -> ArchiveEntry | None:
        return self._by_path.get(path)

    def read(self, entry: ArchiveEntry | str) -> bytes:
        """The unpacked bytes of an entry."""
        if isinstance(entry, str):
            found = self.find(entry)
            if found is None:
                raise FileNotFoundError(entry)
            entry = found
        raw = self.data[entry.data_offset : entry.data_offset + entry.packed_size]
        if not entry.compressed:
            return raw
        out = lz77_decompress(raw)
        if len(out) != entry.unpacked_size:
            raise ArchiveError(
                f"{entry.path}: unpacked {len(out)} bytes, "
                f"table says {entry.unpacked_size}"
            )
        return out


def _cstring(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("ascii", "replace")
