"""A synthetic DSE model built from scratch, so the tests never touch game
data. It follows docs/formats/dse.md: two bones, one packed mesh with
triangles and quads, one CPU-skinned mesh, one material, one raw pal16
texture, and a one-frame rest pose."""

from __future__ import annotations

import math
import struct

HEADER_SIZE = 0x64


def fixed(v: float) -> int:
    return int(round(v * 4096))


def rgb555(r: int, g: int, b: int) -> int:
    return r | (g << 5) | (b << 10)


def pose_record(
    q: tuple[float, float, float, float], t: tuple[float, float, float]
) -> bytes:
    def s12(v: float) -> int:
        return int(round(v * 2048)) & 0xFFF

    def s16(v: float) -> int:
        return int(round(v * 512)) & 0xFFFF

    tz = s16(t[2])
    w0 = (s12(q[1]) << 20) | (s12(q[0]) << 8) | (tz >> 8)
    w1 = (s12(q[3]) << 20) | (s12(q[2]) << 8) | (tz & 0xFF)
    w2 = (s16(t[1]) << 16) | s16(t[0])
    return struct.pack("<3I", w0, w1, w2)


def ds_matrix(rows: list[list[float]], t: list[float]) -> bytes:
    vals = [v for row in rows for v in row] + list(t)
    return struct.pack("<12i", *(fixed(v) for v in vals))


class Strings:
    def __init__(self) -> None:
        self.data = bytearray()
        self.offsets: dict[str, int] = {}

    def add(self, s: str) -> int:
        if s not in self.offsets:
            self.offsets[s] = len(self.data)
            self.data += s.encode() + b"\0"
        return self.offsets[s]


def packed_list(
    verts: list[
        tuple[
            tuple[float, float, float], tuple[float, float], tuple[int, int, int] | None
        ]
    ],
) -> bytes:
    """TEXCOORD, COLOR (if given), VTX_16 per vertex, one packed word each."""
    out = bytearray()
    for pos, uv, color in verts:
        cmds = [0x22] + ([0x20] if color else []) + [0x23]
        word = 0
        for i, c in enumerate(cmds):
            word |= c << (8 * i)
        out += struct.pack("<I", word)
        s, t = (int(round(uv[0] * 16)) & 0xFFFF, int(round(uv[1] * 16)) & 0xFFFF)
        out += struct.pack("<I", s | (t << 16))
        if color:
            out += struct.pack("<I", rgb555(*color))
        x, y, z = (fixed(v) & 0xFFFF for v in pos)
        out += struct.pack("<II", x | (y << 16), z)
    return bytes(out)


def build_model() -> bytes:
    strings = Strings()
    name = strings.add("fixture.dse")
    root = strings.add("root")
    tip = strings.add("tip")
    mesh_a = strings.add("bodyShape")
    mesh_b = strings.add("capeShape")
    mat = strings.add("skin")
    tex_path = strings.add("K:/tex/skin.tm2")
    tex_name = strings.add("skin.tm2")

    # Bones: root at origin, tip 2 units up. Inverse binds are -translation.
    matrices = ds_matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0]) + ds_matrix(
        [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, -2, 0]
    )
    bones = struct.pack("<BBHHHHHhHI", 1, 2, 0x11, 0, 1, root, 0, -1, 0, 0)
    bones += struct.pack("<BBHHHHHhHI", 1, 2, 0x22, 0, 1, tip, 0, 0, 1, 48)

    # Mesh 0: packed triangles (one) then quads (one), on the tip bone.
    tri = packed_list(
        [
            ((0, 0, 0), (0, 0), None),
            ((1, 0, 0), (256, 0), None),
            ((0, 1, 0), (0, 256), None),
        ]
    )
    quad = packed_list(
        [
            ((0, 0, 1), (0, 0), (31, 0, 0)),
            ((1, 0, 1), (256, 0), (31, 0, 0)),
            ((1, 1, 1), (256, 256), (31, 0, 0)),
            ((0, 1, 1), (0, 256), (31, 0, 0)),
        ]
    )
    chunk0 = struct.pack("<BBHI", 2, 0, (31 << 10) | 0, 8)
    chunk0 += struct.pack("<HHIHH", 3, 0, 32 + len(tri), 3, 0x2910) + bytes(20) + tri
    chunk0 += struct.pack("<HHIHH", 4, 0, 32 + len(quad), 4, 0x2D14) + bytes(20) + quad
    chunk0 += struct.pack("<HHI", 1, 0, 8)
    # Mesh 1: one CPU-skinned triangle weighted between both bones.
    raw = b""
    for pos, uv, w in [
        ((0, 2, 0), (0, 0), (1.0, 0.0)),
        ((1, 2, 0), (128, 0), (0.5, 0.5)),
        ((0, 3, 0), (0, 128), (0.0, 1.0)),
    ]:
        raw += struct.pack("<2h", int(uv[0] * 16), int(uv[1] * 16))
        raw += struct.pack("<3h", *(fixed(v) for v in pos))
        raw += struct.pack("<2H", int(w[0] * 4096), int(w[1] * 4096))
    chunk1 = struct.pack("<BBHI", 2, 0, (0 << 10) | 0, 8)  # alpha field unused
    chunk1 += (
        struct.pack("<HHIHHI2H", 3, 0, 32 + len(raw), 3, 0x190E, 10, 0, 1)
        + bytes(12)
        + raw
    )
    chunk1 += struct.pack("<HHI", 1, 0, 8)

    body = bytearray(matrices + bones)
    off0 = len(body)
    body += chunk0
    off1 = len(body)
    body += chunk1

    table_a = struct.pack("<4I", mesh_a, off0, 0x61, rgb555(31, 30, 30))
    table_a += struct.pack(
        "<4I", mesh_b, off1, 0x70 | (1 << 24), rgb555(31, 31, 31) | (1 << 16)
    )
    table_b = struct.pack("<4I", mesh_a, 1, 0, 0) + struct.pack("<4I", mesh_b, 0, 1, 0)
    material = struct.pack("<IHBBH", mat, 0x020F, 31, 0, rgb555(25, 25, 25)) + bytes(6)
    material += bytes([0, 0xFD, 0, 0]) + struct.pack("<II", 1, 1) + bytes(8)
    assert len(material) == 36

    # Texture: 8x8 pal16, raw texels (index = x), 16 colours, colour 0 transparent.
    texels = bytes(
        (x & 0xF) | ((x + 1 & 0xF) << 4) for _y in range(8) for x in range(0, 8, 2)
    )
    palette = b"".join(
        struct.pack("<H", rgb555(i * 2, 0, 31 - i * 2)) for i in range(16)
    )
    texdata = palette + texels
    texture = struct.pack(
        "<6I2H5I",
        tex_path,
        len(palette),
        len(texels),
        len(texels),
        0,
        len(palette),
        8,
        8,
        0,
        0,
        3 | (16 << 8) | (0 << 16) | (1 << 24),
        tex_name,
        0,
    )

    sections = bytearray(body)
    t1 = len(sections)
    sections += table_a
    t2 = len(sections)
    sections += table_b
    t3 = len(sections)
    sections += material
    t4 = len(sections)
    sections += texture
    t5 = len(sections)
    t7 = len(sections)
    sections += bytes(strings.data)
    while len(sections) % 4:
        sections += b"\0"
    frames_abs = HEADER_SIZE + len(sections)
    frames = pose_record((0, 0, 0, 1), (0, 0, 0)) + pose_record((0, 0, 0, 1), (0, 2, 0))
    tex_abs = frames_abs + len(frames)
    end = tex_abs + len(texdata)

    header = bytearray(b"DSE\0" + b"30" + bytes([1, 0x64]) + struct.pack("<H", 5))
    header += b"00000000000".ljust(20, b"\0")
    header += struct.pack("<9H", 0, 0, 2, 2, 2, 1, 1, 0, 0)
    table = [
        96,
        t1,
        t2,
        t3,
        t4,
        t5,
        0,
        t7,
        len(strings.data),
        name,
        frames_abs,
        tex_abs,
        end,
    ]
    header += struct.pack("<13I", *table)
    assert len(header) == HEADER_SIZE
    return bytes(header) + bytes(sections) + frames + texdata


def build_motion(frames: int = 3) -> bytes:
    """A motion set with the fixture's two bones, one clip, and the tip
    rotating 90 degrees about z over the frames."""
    strings = Strings()
    name = strings.add("sm_fixture.dse")
    root = strings.add("root")
    tip = strings.add("tip")
    clip = strings.add("000_spin")
    matrices = ds_matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0]) * 2
    bones = struct.pack("<BBHHHHHhHI", 1, 2, 0x11, 0, frames, root, 0, -1, 0, 0)
    bones += struct.pack("<BBHHHHHhHI", 1, 2, 0x22, 0, frames, tip, 0, 0, 1, 48)
    body = bytearray(matrices + bones)
    t1 = len(body)
    anim = struct.pack("<IHHHH", clip, 0, 7, 1, frames)
    body += anim
    t7 = len(body)
    body += bytes(strings.data)
    while len(body) % 4:
        body += b"\0"
    frames_abs = HEADER_SIZE + len(body)
    poses = b""
    for f in range(frames + 1):
        angle = math.pi / 2 * min(f, frames - 1) / max(frames - 1, 1)
        q = (0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2))
        poses += pose_record((0, 0, 0, 1), (0, 0, 0)) + pose_record(q, (0, 2, 0))
    end = frames_abs + len(poses)
    header = bytearray(b"DSE\0" + b"30" + bytes([0x12, 0x64]) + struct.pack("<H", 1))
    header += b"00000000000".ljust(20, b"\0")
    header += struct.pack("<9H", 0, frames, 2, 0, 0, 0, 0, 0, 1)
    table = [
        96,
        t1,
        t1,
        t1,
        t1,
        t1,
        t1,
        t7,
        len(strings.data),
        name,
        frames_abs,
        end,
        end,
    ]
    header += struct.pack("<13I", *table)
    return bytes(header) + bytes(body) + poses
