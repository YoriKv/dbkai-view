"""The ROM reader and the game-data catalogue, on a synthetic cartridge."""

import struct

import pytest

from dbkai.game import AssetKind, GameData, unwrap
from dbkai.nds.rom import NdsRom, RomError
from tests.dse_fixture import build_model, build_motion
from tests.test_formats import _archive_bytes, lz77_compress_literal


def make_rom(files: dict[str, bytes]) -> bytes:
    """A minimal ROM image: header with a NitroFS whose root holds ``files``
    flat, plus one subdirectory ``debug`` for names containing ``/``."""
    rom = bytearray(0x200)
    rom[0:12] = b"DBK ULTIMATE"
    rom[12:16] = b"TDBJ"
    rom[0x15C:0x15E] = b"\x56\xcf"
    names = sorted(files)
    root = [n for n in names if "/" not in n]
    sub = [n for n in names if "/" in n]
    # FNT: directory table (2 dirs) + name lists.
    fat_entries = len(names)
    fnt = bytearray()
    fnt_dir_size = 8 * 2
    root_names = bytearray()
    first_id = 0
    for n in root:
        root_names += bytes([len(n)]) + n.encode()
    if sub:
        root_names += bytes([0x80 | 5]) + b"debug" + struct.pack("<H", 0xF001)
    root_names += b"\0"
    sub_names = bytearray()
    for n in sub:
        base = n.split("/", 1)[1]
        sub_names += bytes([len(base)]) + base.encode()
    sub_names += b"\0"
    fnt += struct.pack("<IHH", fnt_dir_size, first_id, 2)
    fnt += struct.pack("<IHH", fnt_dir_size + len(root_names), len(root), 0xF000)
    fnt += root_names + sub_names
    fnt_off = 0x200
    fat_off = (fnt_off + len(fnt) + 3) & ~3
    data_off = fat_off + 8 * fat_entries
    fat = bytearray()
    blob = bytearray()
    for n in root + sub:
        start = data_off + len(blob)
        blob += files[n]
        while len(blob) % 4:
            blob += b"\0"
        fat += struct.pack("<II", start, start + len(files[n]))
    struct.pack_into("<IIII", rom, 0x40, fnt_off, len(fnt), fat_off, len(fat))
    return (
        bytes(rom)
        + bytes(fnt)
        + bytes(fat_off - fnt_off - len(fnt))
        + bytes(fat)
        + bytes(blob)
    )


@pytest.fixture
def rom_bytes():
    archive = _archive_bytes(
        [
            ("/mdl/chr", "101100_hero.dse", build_model(), True),
            ("/mdl/chr", "nt_101100_hero.dse", build_model(), False),
        ]
    )
    return make_rom(
        {
            "archiveDBK.dsa": archive,
            "sm_100000_NORMAL.dse": build_motion(),
            "readme.txt": b"hi",
            "debug/cube.dse7": lz77_compress_literal(build_model()),
        }
    )


def test_rom_lists_files(rom_bytes):
    rom = NdsRom(rom_bytes)
    assert rom.header.game_code == "TDBJ"
    paths = sorted(str(f.path) for f in rom.files)
    assert paths == [
        "/archiveDBK.dsa",
        "/debug/cube.dse7",
        "/readme.txt",
        "/sm_100000_NORMAL.dse",
    ]
    assert rom.read("/readme.txt") == b"hi"
    assert rom.find("/missing") is None
    with pytest.raises(RomError):
        NdsRom(bytes(0x200))


def test_game_catalogue_and_relations(rom_bytes):
    game = GameData(NdsRom(rom_bytes))
    kinds = {a.path: a.kind for a in game.assets}
    assert kinds == {
        "/archiveDBK.dsa/mdl/chr/101100_hero.dse": AssetKind.MODEL,
        "/archiveDBK.dsa/mdl/chr/nt_101100_hero.dse": AssetKind.MODEL,
        "/sm_100000_NORMAL.dse": AssetKind.MOTION_SET,
        "/debug/cube.dse7": AssetKind.MODEL,
    }
    hero = game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse")
    assert hero.numeric_id == 101100 and hero.body_type == 100000
    assert game.motion_set_for(hero).path == "/sm_100000_NORMAL.dse"
    nt = game.find("/archiveDBK.dsa/mdl/chr/nt_101100_hero.dse")
    assert game.textured_sibling(nt) is hero
    model = game.load_model(hero)
    assert model.name == "101100_hero.dse" and len(model.meshes) == 2
    cube = game.load_model(game.find("/debug/cube.dse7"))
    assert len(cube.meshes) == 2
    assert unwrap(b"DSE\0" + bytes(100)) == b"DSE\0" + bytes(100)
