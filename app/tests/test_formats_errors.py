"""The decoders on malformed synthetic data: each failure surfaces as the
format's own error, and the fields the docs pin down are read as they say."""

import struct

import pytest

from dbkai.formats import archive, dsa, dse, texture
from dbkai.nds.rom import NdsRom, RomError
from tests.dsa_fixture import build_actions
from tests.dse_fixture import HEADER_SIZE, build_model
from tests.test_formats import _archive_bytes


def _section(data: bytes, n: int) -> int:
    """The file offset of section ``n`` of a DSE file (``0x64 + T[n]``)."""
    return HEADER_SIZE + struct.unpack_from("<I", data, 0x30 + 4 * n)[0]


def test_dse_table_past_the_end_is_a_dse_error():
    data = bytearray(build_model())
    struct.pack_into("<H", data, 0x24, 0xFFFF)  # mesh count
    with pytest.raises(dse.DseError):
        dse.parse(bytes(data))


def test_dse_unknown_texture_format_is_a_dse_error():
    data = bytearray(build_model())
    data[_section(data, 4) + 0x24] = 9
    with pytest.raises(dse.DseError):
        dse.parse(bytes(data))


def test_material_select_reads_only_the_low_byte():
    # The draw routine reads the low byte of a material-select chunk's value;
    # bits above it (the alpha field starts at bit 10) never pick a material.
    data = bytearray(build_model())
    chunk = HEADER_SIZE + struct.unpack_from("<I", data, _section(data, 1) + 4)[0]
    value = struct.unpack_from("<H", data, chunk + 2)[0]
    struct.pack_into("<H", data, chunk + 2, value | 0x300)
    body = dse.parse(bytes(data)).meshes[0]
    assert body.material == 0
    assert body.materials == [0]


def _skinned(header: int, vertex_count: int, data: bytes) -> dse.DisplayList:
    stride = header + 4  # two bone weights
    return dse.DisplayList(
        dse.Primitive.TRIANGLES,
        vertex_count,
        0x1800 | stride,
        data,
        bones=(0, 1),
        header=header,
    )


def test_skinned_weights_start_at_the_header_offset():
    # s, t, x, y, z, two bytes of padding, then the weights at offset 12.
    vertex = struct.pack("<2h3h", 16, 32, 4096, 0, 0) + b"\xee\xee"
    vertex += struct.pack("<2H", 1024, 3072)
    (v,) = _skinned(12, 1, vertex).skinned_vertices()
    assert v.uv == (1.0, 2.0) and v.position == (1.0, 0.0, 0.0)
    assert v.weights == (0.25, 0.75)


def test_skinned_list_shorter_than_its_vertices_is_a_dse_error():
    vertex = struct.pack("<2h3h2H", 0, 0, 0, 0, 0, 4096, 0)
    with pytest.raises(dse.DseError):
        _skinned(10, 2, vertex).skinned_vertices()


def test_texture_format_none_is_a_texture_error():
    with pytest.raises(texture.TextureError):
        texture.decode(texture.TextureFormat.NONE, 1, 1, b"\0\0")


def test_archive_entry_past_the_end_is_an_archive_error():
    data = _archive_bytes([("/mdl", "a.dse", b"raw-bytes", False)])
    a = archive.DsaArchive(data[:-3])
    with pytest.raises(archive.ArchiveError):
        a.read("/mdl/a.dse")


def test_archive_truncated_tables_are_an_archive_error():
    with pytest.raises(archive.ArchiveError):
        archive.DsaArchive(struct.pack("<4sIII", b"DSA ", 1, 0, 5) + bytes(8))


def test_dsa_truncated_tables_are_a_dsa_error():
    data = bytearray(build_actions())
    struct.pack_into("<H", data, 0x0A, 0xFFFF)  # record count
    with pytest.raises(dsa.DsaError):
        dsa.parse(bytes(data))


def test_dsa_track_past_the_end_is_dropped():
    data = bytearray(build_actions())
    commands = struct.unpack_from("<I", data, 0x14)[0]
    record = commands + struct.unpack_from("<I", data, 0x24 + 4 * 5)[0]
    # The groups track's header fits in the file's last bytes; its five keys
    # do not.
    data[-4:] = struct.pack("<BBh", 1, 5, 0)
    struct.pack_into("<I", data, record + 0x1C, len(data) - 4 - commands)
    tracked = dsa.parse(bytes(data)).actions[1].commands[0]
    assert isinstance(tracked, dsa.VisibilityCommand)
    assert tracked.groups_track is None and tracked.parts_track is not None


def _rom(fnt: bytes) -> bytes:
    rom = bytearray(0x200)
    rom[0x15C:0x15E] = b"\x56\xcf"
    struct.pack_into("<IIII", rom, 0x40, 0x200, len(fnt), 0x200 + len(fnt), 0)
    return bytes(rom) + fnt


def test_rom_directory_loop_is_a_rom_error():
    # The root holds one subdirectory, "a", whose id is the root's own.
    fnt = struct.pack("<IHH", 8, 0, 1) + bytes([0x81]) + b"a"
    fnt += struct.pack("<H", 0xF000) + b"\0"
    with pytest.raises(RomError):
        NdsRom(_rom(fnt))


def test_rom_fnt_past_the_end_is_a_rom_error():
    fnt = struct.pack("<IHH", 0x1000, 0, 1)
    with pytest.raises(RomError):
        NdsRom(_rom(fnt))
