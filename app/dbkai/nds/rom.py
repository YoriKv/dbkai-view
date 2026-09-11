"""A Nintendo DS cartridge image: header fields and the NitroFS tree.

Only the parts the extractor needs: where the file system is, and how to walk
it. Nothing here knows about the game's own formats.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_ROOT_DIR = 0xF000


class RomError(ValueError):
    """The image is not a DS ROM, or its file system does not parse."""


@dataclass(frozen=True)
class RomFile:
    """One NitroFS file: its path inside the image and where its bytes are."""

    path: PurePosixPath
    file_id: int
    offset: int
    size: int

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class RomHeader:
    title: str
    game_code: str
    arm9_offset: int
    arm9_size: int
    arm7_offset: int
    arm7_size: int
    fnt_offset: int
    fnt_size: int
    fat_offset: int
    fat_size: int


class NdsRom:
    """A ROM image held in memory, with its NitroFS listed on construction."""

    def __init__(self, data: bytes, source: Path | None = None) -> None:
        if len(data) < 0x200 or data[0x15C:0x15E] != b"\x56\xcf":
            raise RomError("not a Nintendo DS ROM image (bad Nintendo logo CRC)")
        self.data = data
        self.source = source
        self.header = RomHeader(
            title=data[0:12].rstrip(b"\0").decode("ascii", "replace"),
            game_code=data[12:16].decode("ascii", "replace"),
            arm9_offset=struct.unpack_from("<I", data, 0x20)[0],
            arm9_size=struct.unpack_from("<I", data, 0x2C)[0],
            arm7_offset=struct.unpack_from("<I", data, 0x30)[0],
            arm7_size=struct.unpack_from("<I", data, 0x3C)[0],
            fnt_offset=struct.unpack_from("<I", data, 0x40)[0],
            fnt_size=struct.unpack_from("<I", data, 0x44)[0],
            fat_offset=struct.unpack_from("<I", data, 0x48)[0],
            fat_size=struct.unpack_from("<I", data, 0x4C)[0],
        )
        self.files: list[RomFile] = list(self._walk())
        self._by_path = {str(f.path): f for f in self.files}

    @classmethod
    def open(cls, path: str | Path) -> NdsRom:
        path = Path(path)
        return cls(path.read_bytes(), source=path)

    # -- NitroFS --------------------------------------------------------------

    def _fat_entry(self, file_id: int) -> tuple[int, int]:
        h = self.header
        if file_id * 8 + 8 > h.fat_size:
            raise RomError(f"file id {file_id} is outside the FAT")
        start, end = struct.unpack_from("<II", self.data, h.fat_offset + 8 * file_id)
        if end < start or end > len(self.data):
            raise RomError(f"FAT entry {file_id} points outside the image")
        return start, end - start

    def _walk(self) -> Iterator[RomFile]:
        fnt = self.header.fnt_offset
        data = self.data

        def directory(dir_id: int, prefix: PurePosixPath) -> Iterator[RomFile]:
            sub_offset, first_id = struct.unpack_from(
                "<IH", data, fnt + 8 * (dir_id & 0xFFF)
            )
            p = fnt + sub_offset
            file_id = first_id
            while True:
                kind = data[p]
                p += 1
                if kind == 0:
                    return
                length = kind & 0x7F
                name = data[p : p + length].decode("ascii", "replace")
                p += length
                if kind & 0x80:
                    sub_id = struct.unpack_from("<H", data, p)[0]
                    p += 2
                    yield from directory(sub_id, prefix / name)
                else:
                    offset, size = self._fat_entry(file_id)
                    yield RomFile(prefix / name, file_id, offset, size)
                    file_id += 1

        yield from directory(_ROOT_DIR, PurePosixPath("/"))

    def find(self, path: str) -> RomFile | None:
        """The file at ``path`` (``/debug/goku/goku.dse``), or ``None``."""
        if not path.startswith("/"):
            path = "/" + path
        return self._by_path.get(path)

    def read(self, file: RomFile | str) -> bytes:
        """The bytes of a file, by :class:`RomFile` or path."""
        if isinstance(file, str):
            found = self.find(file)
            if found is None:
                raise FileNotFoundError(file)
            file = found
        return self.data[file.offset : file.offset + file.size]
