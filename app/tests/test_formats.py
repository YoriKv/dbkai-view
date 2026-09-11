"""The decoders, on synthetic data."""

import struct

import pytest

from dbkai.formats import archive, compression, dse, gx, texture
from tests.dse_fixture import build_model, build_motion


def lz77_compress_literal(data: bytes) -> bytes:
    """A valid BIOS LZ77 stream that uses only literals."""
    out = bytearray(struct.pack("<I", 0x10 | (len(data) << 8)))
    for i in range(0, len(data), 8):
        chunk = data[i : i + 8]
        out.append(0)
        out += chunk
    return bytes(out)


def test_lz77_literals_and_references():
    # "abcabcabc": three literals, then a reference of length 6 at distance 3.
    stream = (
        struct.pack("<I", 0x10 | (9 << 8))
        + bytes([0b00010000])
        + b"abc"
        + bytes([0x30, 0x02])
    )
    assert compression.lz77_decompress(stream) == b"abcabcabc"
    assert compression.lz77_decompress(lz77_compress_literal(b"hello")) == b"hello"
    with pytest.raises(compression.CompressionError):
        compression.lz77_decompress(b"\x11\x00\x00\x00")


def test_lzss_run_and_literals():
    # Flag 0b11111101: literal 'A', then a reference at the write cursor
    # (0xFEE, length 9) that repeats it, then six literals.
    stream = bytes([0xFD, 0x41, 0xEE, 0xF6, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47])
    assert compression.lzss_decompress(stream, 16) == b"A" * 10 + b"BCDEFG"
    # A reference into the untouched ring reads zeros.
    stream = bytes([0xFE, 0x00, 0xF0, 0x41])
    assert compression.lzss_decompress(stream, 4) == bytes(3) + b"A"
    with pytest.raises(compression.CompressionError):
        compression.lzss_decompress(b"\x01", 4)


def test_lzma_detection():
    assert compression.is_lzma_alone(b"\x5d\x00\x00\x01\x00" + bytes(8))
    assert not compression.is_lzma_alone(b"DSE\0" + bytes(20))


def test_gx_decode_all_vertex_forms():
    words = [
        0x24232220,  # COLOR, TEXCOORD, VTX_16, VTX_10
        texture.rgb555_to_rgb8(0) and 0x7FFF,  # white
        (32 << 4) | ((16 << 4) << 16),  # s=32, t=16 texels
        (0x1000) | (0x2000 << 16),
        0x0800,  # (1.0, 2.0, 0.5)
        (0x040 << 0) | (0x080 << 10) | (0x0C0 << 20),  # VTX_10: (1, 2, 3)
        0x00002825,  # VTX_XY, VTX_DIFF
        0x1000 | (0x1000 << 16),  # x=1, y=1 (z stays 3)
        (0x3FF) | (1 << 10),  # dx=-1/4096, dy=+1/4096
    ]
    verts = gx.decode_vertices(struct.pack(f"<{len(words)}I", *words))
    assert len(verts) == 4
    assert verts[0].position == (1.0, 2.0, 0.5)
    assert verts[0].uv == (32.0, 16.0)
    assert verts[0].color == (31, 31, 31)
    assert verts[1].position == (1.0, 2.0, 3.0)
    assert verts[2].position == (1.0, 1.0, 3.0)
    assert verts[3].position == pytest.approx((1 - 1 / 4096, 1 + 1 / 4096, 3.0))
    with pytest.raises(gx.DisplayListError):
        gx.decode_vertices(struct.pack("<I", 0xF0))


def test_texture_pal16_and_transparency():
    palette = b"".join(struct.pack("<H", i | (i << 5) | (i << 10)) for i in range(16))
    texels = bytes([0x10, 0x32])  # indices 0,1,2,3 in a 4x1 texture
    img = texture.decode(texture.TextureFormat.PAL16, 4, 1, texels, palette, True)
    px = [tuple(img.pixels[i : i + 4]) for i in range(0, 16, 4)]
    assert px[0][3] == 0 and px[1][3] == 255
    assert px[3][:3] == (3 * 255 // 31,) * 3
    a5i3 = texture.decode(
        texture.TextureFormat.A5I3, 1, 1, bytes([0b11111_001]), palette
    )
    assert a5i3.pixels[3] == 255 and a5i3.pixels[0] == 255 // 31
    with pytest.raises(texture.TextureError):
        texture.decode(texture.TextureFormat.PAL256, 8, 8, b"", palette)


def test_dse_model_parses():
    f = dse.parse(build_model())
    assert f.name == "fixture.dse"
    assert f.is_model and not f.is_motion
    assert [b.name for b in f.bones] == ["root", "tip"]
    assert f.bones[1].parent == 0
    assert f.bones[1].inverse_bind.translation == (0.0, -2.0, 0.0)
    assert [m.name for m in f.meshes] == ["bodyShape", "capeShape"]
    body, cape = f.meshes
    assert body.bone == 1 and body.has_vertex_colors and not body.double_sided
    assert [dl.primitive for dl in body.display_lists] == [
        dse.Primitive.TRIANGLES,
        dse.Primitive.QUADS,
    ]
    assert body.display_lists[0].vertices()[1].position == (1.0, 0.0, 0.0)
    assert body.display_lists[1].vertices()[0].color == (31, 0, 0)
    assert cape.group == 1 and cape.shift == 1 and cape.alpha == 31
    sk = cape.display_lists[0]
    assert sk.is_skinned and sk.bones == (0, 1)
    v = sk.skinned_vertices()
    assert v[1].weights == (0.5, 0.5) and v[2].position == (0.0, 3.0, 0.0)
    assert (
        f.materials[0].texture == 0
        and f.materials[0].repeat_s
        and f.materials[0].flip_t
    )
    t = f.textures[0]
    assert (t.width, t.height, t.format) == (8, 8, texture.TextureFormat.PAL16)
    assert t.has_data and t.decode().pixels[3] == 255
    assert t.decode(color0_transparent=True).pixels[3] == 0
    assert f.frame_count == 1
    assert f.pose(0, 1).translation == (0.0, 2.0, 0.0)


def test_dse_pose_record_roundtrip():
    from tests.dse_fixture import pose_record

    p = dse.decode_pose(pose_record((0.5, -0.25, 0.125, 0.8125), (1.5, -2.0, 3.25)))
    assert p.rotation == pytest.approx((0.5, -0.25, 0.125, 0.8125))
    assert p.translation == (1.5, -2.0, 3.25)


def test_dse_motion_parses():
    f = dse.parse(build_motion(frames=3))
    assert f.is_motion and not f.is_model
    assert f.frame_count == 4
    assert len(f.animations) == 1 and f.animations[0].frame_count == 3
    q = f.pose(2, 1).rotation
    assert q[2] == pytest.approx(0.7071, abs=1e-3) and q[3] == pytest.approx(
        0.7071, abs=1e-3
    )


def test_dse_rejects_garbage():
    with pytest.raises(dse.DseError):
        dse.parse(b"NOPE" + bytes(200))


def _archive_bytes(entries: list[tuple[str, str, bytes, bool]]) -> bytes:
    """(directory, name, data, compressed) -> an archive image."""
    dirs = sorted({d for d, _, _, _ in entries})
    header_size = 16 + 12 * len(dirs) + 20 * len(entries)
    names = b"".join(d.encode() + b"\0" for d in dirs)
    data_start = header_size + len(names)
    blob = bytearray()
    table = bytearray()
    for _directory, n, data, compressed in entries:
        packed = lz77_compress_literal(data) if compressed else data
        name = n.encode() + b"\0"
        table += struct.pack(
            "<IIIII", len(name), 7, len(data), len(packed), data_start + len(blob)
        )
        blob += name + packed
    dir_table = bytearray()
    name_off = header_size
    for d in dirs:
        idx = [i for i, e in enumerate(entries) if e[0] == d]
        dir_table += struct.pack("<III", idx[0], len(idx), name_off)
        name_off += len(d) + 1
    return (
        struct.pack("<4sIII", b"DSA ", 1, data_start, len(dirs))
        + dir_table
        + table
        + names
        + blob
    )


def test_archive_lists_and_unpacks():
    a = archive.DsaArchive(
        _archive_bytes(
            [
                ("/mdl", "a.dse", b"raw-bytes", False),
                ("/mdl", "b.dse", b"packed!", True),
            ]
        )
    )
    assert [e.path for e in a.entries] == ["/mdl/a.dse", "/mdl/b.dse"]
    assert not a.entries[0].compressed and a.entries[1].compressed
    assert a.read("/mdl/a.dse") == b"raw-bytes"
    assert a.read(a.entries[1]) == b"packed!"
    with pytest.raises(archive.ArchiveError):
        archive.DsaArchive(b"NOPE" + bytes(32))
