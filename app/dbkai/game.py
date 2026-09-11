"""Everything in one ROM, catalogued: which files are models, motions and
textures, where they live, and how they relate.

:class:`GameData` opens the cartridge and lists :class:`Asset` records without
decoding anything; :meth:`GameData.load_model` and friends decode on demand.
The relations it knows:

- an ``nt_`` model has no texture data of its own; its textures are in the
  sibling entry without the prefix;
- a character model's motions are the ``smot/sm_<body>_*.dse`` set for its
  body type, which is the ``<id> // 10000 * 10000`` of the model's numeric id;
- the story packages (``sp/*.dsdz``) embed models and motions, which are
  listed as assets inside the package.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path, PurePosixPath

from dbkai.formats import compression, dse
from dbkai.formats.archive import ArchiveEntry, DsaArchive
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.nds.rom import NdsRom, RomFile

ARCHIVE_PATH = "/archiveDBK.dsa"
_ID_PREFIX = re.compile(r"^(?:nt_|sm_|st_)?(\d+)")


class AssetKind(Enum):
    MODEL = "model"
    MOTION = "motion"
    MOTION_SET = "motion set"
    TEXTURES = "textures"
    OTHER = "other"


@dataclass(frozen=True)
class Asset:
    """One file worth showing: its virtual path, what it is, and where the
    bytes come from (a NitroFS file, or an archive entry)."""

    path: str
    kind: AssetKind
    size: int
    rom_file: RomFile | None = None
    entry: ArchiveEntry | None = None
    packed_size: int | None = None
    #: For a file embedded in a package: the package and the byte offset.
    container: Asset | None = None
    offset: int = 0

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def directory(self) -> str:
        return str(PurePosixPath(self.path).parent)

    @property
    def numeric_id(self) -> int | None:
        m = _ID_PREFIX.match(self.name)
        return int(m.group(1)) if m else None

    @property
    def is_untextured_variant(self) -> bool:
        return self.name.startswith("nt_")

    @property
    def body_type(self) -> int | None:
        """The motion set id a character model animates with, or ``None``."""
        n = self.numeric_id
        if n is None or not 100000 <= n < 200000:
            return None
        return n // 10000 * 10000


class GameData:
    def __init__(self, rom: NdsRom) -> None:
        self.rom = rom

    @classmethod
    def open(cls, path: str | Path) -> GameData:
        return cls(NdsRom.open(path))

    @cached_property
    def archive(self) -> DsaArchive | None:
        f = self.rom.find(ARCHIVE_PATH)
        return DsaArchive(self.rom.read(f)) if f else None

    @cached_property
    def assets(self) -> list[Asset]:
        return list(self._catalogue())

    @cached_property
    def _by_path(self) -> dict[str, Asset]:
        return {a.path: a for a in self.assets}

    def find(self, path: str) -> Asset | None:
        return self._by_path.get(path)

    def _catalogue(self) -> Iterator[Asset]:
        for f in self.rom.files:
            if str(f.path) == ARCHIVE_PATH:
                continue
            kind = _kind_from_name(f.name)
            if kind is AssetKind.OTHER:
                continue
            yield Asset(str(f.path), kind, f.size, rom_file=f)
        if self.archive:
            for e in self.archive.entries:
                kind = _kind_from_name(e.name)
                if kind is AssetKind.OTHER:
                    continue
                yield Asset(
                    f"{ARCHIVE_PATH}{e.path}",
                    kind,
                    e.unpacked_size,
                    entry=e,
                    packed_size=e.packed_size,
                )
        for f in self.rom.files:
            if f.name.lower().endswith(".dsdz"):
                yield from self._embedded(f)

    def _embedded(self, f: RomFile) -> Iterator[Asset]:
        """The DSE files inside a story package, found by their magic."""
        package = Asset(str(f.path), AssetKind.OTHER, f.size, rom_file=f)
        try:
            data = unwrap(self.rom.read(f))
        except compression.CompressionError:
            return
        for m in re.finditer(re.escape(dse.MAGIC), data):
            start = m.start()
            if start + dse.HEADER_SIZE > len(data):
                continue
            size = struct.unpack_from("<I", data, start + 0x30 + 12 * 4)[0]
            if size < dse.HEADER_SIZE or start + size > len(data):
                continue
            try:
                file = dse.parse(data[start : start + size])
            except dse.DseError:
                continue
            kind = classify(file)
            if kind is AssetKind.OTHER:
                continue
            name = file.name or f"{start:#x}.dse"
            yield Asset(f"{f.path}/{name}", kind, size, container=package, offset=start)

    # -- reading --------------------------------------------------------------

    def read(self, asset: Asset) -> bytes:
        """The asset's bytes, unpacked from whatever wrapped them."""
        if asset.container is not None:
            package = self.read(asset.container)
            return package[asset.offset : asset.offset + asset.size]
        if asset.entry is not None:
            assert self.archive is not None
            data = self.archive.read(asset.entry)
        elif asset.rom_file is not None:
            data = self.rom.read(asset.rom_file)
        else:
            raise ValueError(f"{asset.path} has no source")
        return unwrap(data)

    def load_dse(self, asset: Asset) -> dse.DseFile:
        return dse.parse(self.read(asset))

    def load_model(self, asset: Asset) -> scene.Model:
        """Build the model, borrowing textures from the textured sibling of
        an ``nt_`` entry."""
        file = self.load_dse(asset)
        if asset.is_untextured_variant:
            sibling = self.textured_sibling(asset)
            if sibling is not None:
                textured = self.load_dse(sibling)
                if len(textured.textures) == len(file.textures):
                    file.textures = textured.textures
        return scene.build(file, asset.name)

    def load_motion(self, asset: Asset) -> Motion:
        return Motion(self.load_dse(asset), asset.name)

    # -- relations ------------------------------------------------------------

    def textured_sibling(self, asset: Asset) -> Asset | None:
        if not asset.is_untextured_variant:
            return None
        return self.find(f"{asset.directory}/{asset.name[3:]}")

    def motion_sets(self) -> list[Asset]:
        return [a for a in self.assets if a.kind is AssetKind.MOTION_SET]

    def motion_set_for(self, asset: Asset) -> Asset | None:
        body = asset.body_type
        if body is None:
            return None
        for a in self.motion_sets():
            if a.numeric_id == body:
                return a
        return None

    def motions_for(self, asset: Asset) -> list[Asset]:
        """Motion files that share the model's numeric id prefix (a prop's
        own animations), plus its body type's set."""
        out = []
        n = asset.numeric_id
        for a in self.assets:
            if (
                a.kind in (AssetKind.MOTION, AssetKind.MOTION_SET)
                and n is not None
                and a.numeric_id == n
                and a is not asset
            ):
                out.append(a)
        s = self.motion_set_for(asset)
        if s is not None and s not in out:
            out.append(s)
        return out


def unwrap(data: bytes) -> bytes:
    """Strip the LZMA or LZ77 wrapper a file may carry."""
    if dse.is_dse(data):
        return data
    if compression.is_lzma_alone(data):
        return compression.lzma_alone_decompress(data)
    if compression.is_lz77(data):
        try:
            return compression.lz77_decompress(data)
        except compression.CompressionError:
            return data
    return data


def _kind_from_name(name: str) -> AssetKind:
    lower = name.lower()
    stem = lower.split(".")[0]
    if lower.endswith((".dse", ".dsez", ".dse7")):
        if stem.startswith("sm_"):
            return AssetKind.MOTION_SET
        if stem.startswith("st_"):
            return AssetKind.TEXTURES
        return AssetKind.MODEL
    return AssetKind.OTHER


def classify(file: dse.DseFile) -> AssetKind:
    """What a parsed file actually holds, once its contents are known."""
    if file.meshes:
        return AssetKind.MODEL
    if file.animations:
        return AssetKind.MOTION_SET
    if file.frame_count > 1 and file.bones:
        return AssetKind.MOTION
    if file.textures:
        return AssetKind.TEXTURES
    return AssetKind.OTHER
