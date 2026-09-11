"""``DSE`` files: models, motion sets and single motions, in one container.

The layout is documented in ``docs/formats/dse.md``. In short: a 0x64-byte
header with counts and a 13-entry section table; the bones' inverse bind
matrices; the bone records; a chunk stream holding each mesh's display lists;
tables for meshes, materials, textures and animations; a string table; then
the pose frames and the packed texture data.

:func:`parse` returns a :class:`DseFile` with everything decoded except the
display lists' vertices and the textures' pixels, which are decoded on demand
through :meth:`DisplayList.vertices` and :meth:`Texture.decode`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from functools import cached_property

from dbkai.formats import gx, texture
from dbkai.formats.compression import CompressionError, lz77_decompress, lzss_decompress

MAGIC = b"DSE\0"
HEADER_SIZE = 0x64
_MATRIX_SIZE = 48
_BONE_SIZE = 20
_MESH_SIZE = 16
_MATERIAL_SIZE = 36
_TEXTURE_SIZE = 48
_ANIMATION_SIZE = 12
POSE_SIZE = 12

#: The material's texture index meaning "untextured".
NO_TEXTURE = 0xFD


class DseError(ValueError):
    """The bytes are not a DSE file this parser understands."""


class DseKind(IntEnum):
    """Bits of the header's kind byte. A file is usually one of MODEL,
    MOTION, MOTION_SET or STAGE_TEXTURES, but a prop can be MODEL | MOTION."""

    MODEL = 0x01
    MOTION = 0x02
    STAGE_TEXTURES = 0x08
    MOTION_SET = 0x10
    UNKNOWN_20 = 0x20
    PATCHED = 0x40  # set in RAM once the game has relocated the file


class Primitive(IntEnum):
    """A display list chunk's primitive type: its chunk type byte."""

    TRIANGLES = 3
    QUADS = 4

    @property
    def vertices_per_face(self) -> int:
        return 3 if self is Primitive.TRIANGLES else 4


@dataclass(frozen=True)
class Matrix4x3:
    """A DS 4x3 matrix as the twelve 20.12 values in file order: three rows of
    the 3x3 part, then the translation row. The hardware applies it to row
    vectors, so ``x' = x*m[0] + y*m[3] + z*m[6] + m[9]``."""

    values: tuple[float, ...]

    @property
    def translation(self) -> tuple[float, float, float]:
        return self.values[9:12]  # type: ignore[return-value]

    @property
    def is_identity(self) -> bool:
        return self.values == (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)


@dataclass(frozen=True)
class Bone:
    index: int
    name: str
    name_hash: int
    parent: int  # -1 for a root
    flags: int
    inverse_bind: Matrix4x3
    unknown_a: int
    unknown_b: int
    unknown_c: int

    @property
    def is_root(self) -> bool:
        return self.parent < 0


@dataclass(frozen=True)
class SkinnedVertex:
    """A vertex of a CPU-skinned list: model-space position, raw 12.4 texture
    coordinate, optional 5-bit colour, and one weight per bone in the list's
    ``bones``."""

    position: tuple[float, float, float]
    uv: tuple[float, float]
    color: tuple[int, int, int] | None
    weights: tuple[float, ...]


@dataclass(frozen=True)
class DisplayList:
    """One chunk of geometry, all of one primitive type.

    Most lists are packed geometry commands the game DMAs straight to the
    GPU; :meth:`vertices` decodes those. A list whose layout has bit 4 set is
    instead a raw vertex array the game skins on the CPU against ``bones``;
    :meth:`skinned_vertices` decodes those and :meth:`vertices` returns them
    with weights dropped, positioned in model space.
    """

    primitive: Primitive
    vertex_count: int
    layout: int
    data: bytes
    bones: tuple[int, ...] = ()
    header: int = 0
    material: int = 0
    alpha: int = 31

    @property
    def is_skinned(self) -> bool:
        return bool(self.layout & 0x1000)

    @property
    def stride(self) -> int:
        return self.layout & 0xFF

    def vertices(self) -> list[gx.Vertex]:
        if self.is_skinned:
            return [
                gx.Vertex(v.position, v.uv, v.color) for v in self.skinned_vertices()
            ]
        return gx.decode_vertices(self.data)

    def skinned_vertices(self) -> list[SkinnedVertex]:
        """Decode a raw vertex array: the 12.4 texture coordinate, a 5-bit
        colour word when layout bit 2 is set, the position (three 4.12
        words), then the weights (4.12) from the byte offset ``header``."""
        if not self.is_skinned:
            raise DseError("not a CPU-skinned list")
        stride = self.stride
        has_color = bool(self.layout & 0x400)
        weights_n = len(self.bones)
        if stride != self.header + 2 * weights_n:
            raise DseError(
                f"skinned list stride {stride} does not fit {weights_n} bone weights"
            )
        out: list[SkinnedVertex] = []
        fmt = struct.Struct(f"<2h{'H' if has_color else ''}3h{weights_n}H")
        for i in range(self.vertex_count):
            f = fmt.unpack_from(self.data, i * stride)
            uv = (f[0] / 16, f[1] / 16)
            k = 2
            color = None
            if has_color:
                c = f[k]
                k += 1
                color = (c & 0x1F, (c >> 5) & 0x1F, (c >> 10) & 0x1F)
            pos = (f[k] / 4096, f[k + 1] / 4096, f[k + 2] / 4096)
            out.append(
                SkinnedVertex(pos, uv, color, tuple(w / 4096 for w in f[k + 3 :]))
            )
        return out


@dataclass(frozen=True)
class Mesh:
    index: int
    name: str
    bone: int
    material: int
    alpha: int
    flags: int
    group: int
    color: tuple[int, int, int]
    display_lists: tuple[DisplayList, ...]
    chunk_flags: int = 0
    shift: int = 0

    @property
    def has_vertex_colors(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def double_sided(self) -> bool:
        """Drawn with both faces: flag ``0x08`` sets the DS cull mode to
        none, flag ``0x04`` draws the list twice, back faces then front."""
        return bool(self.flags & 0x0C)

    @property
    def back_faces_only(self) -> bool:
        """Flag ``0x10``: the game culls the front faces instead of the back,
        so the mesh's winding is reversed (the inner side of hair pieces)."""
        return bool(self.flags & 0x10) and not self.double_sided

    @property
    def fog(self) -> bool:
        return bool(self.flags & 0x20)

    @property
    def box_test(self) -> bool:
        """The game runs a BOX_TEST on the twelve bytes before the mesh chunk
        and skips the mesh when the box is off screen."""
        return bool(self.flags & 0x100)

    @property
    def vertex_count(self) -> int:
        return sum(dl.vertex_count for dl in self.display_lists)

    @property
    def materials(self) -> list[int]:
        """Every material the mesh's lists use, first use first. A mesh
        switches material mid-stream when, say, a trouser leg continues past
        the skin of the ankle."""
        seen: list[int] = []
        for dl in self.display_lists:
            if dl.material not in seen:
                seen.append(dl.material)
        return seen


@dataclass(frozen=True)
class Material:
    index: int
    name: str
    texture: int | None
    part: int
    wrap: int  # TEXIMAGE_PARAM bits 16-19: repeat S, repeat T, flip S, flip T
    diffuse: tuple[int, int, int]
    raw: bytes = field(repr=False)

    @property
    def textured(self) -> bool:
        return bool(self.raw[4] & 1)

    @property
    def alpha(self) -> int:
        return self.raw[6]

    @property
    def repeat_s(self) -> bool:
        return bool(self.wrap & 1)

    @property
    def repeat_t(self) -> bool:
        return bool(self.wrap & 2)

    @property
    def flip_s(self) -> bool:
        return bool(self.wrap & 4)

    @property
    def flip_t(self) -> bool:
        return bool(self.wrap & 8)


@dataclass(frozen=True)
class Texture:
    index: int
    name: str
    path: str
    width: int
    height: int
    format: texture.TextureFormat
    palette_count: int
    texels: bytes = field(repr=False)
    palettes: tuple[bytes, ...] = field(repr=False)
    raw_format: int = 0

    @property
    def has_data(self) -> bool:
        """``False`` for the ``nt_`` ("no texture") models, whose table lists
        textures that live in the sibling file without the prefix."""
        return bool(self.texels)

    def decode(
        self, palette: int = 0, color0_transparent: bool = False
    ) -> texture.Rgba:
        if not self.has_data:
            raise DseError(f"texture {self.name} has no pixel data in this file")
        return texture.decode(
            self.format,
            self.width,
            self.height,
            self.texels,
            self.palettes[palette] if self.palettes else b"",
            color0_transparent,
        )


@dataclass(frozen=True)
class Animation:
    """One clip of a motion set: a run of frames in the file's frame array.
    ``first`` and ``last`` are the 1-based frame numbers of the source scene,
    which is how the set records that several clips came from one take."""

    index: int
    name: str
    start: int
    first: int
    last: int
    unknown: int

    @property
    def frame_count(self) -> int:
        return self.last - self.first + 1


@dataclass(frozen=True)
class Pose:
    """One bone's local transform in a frame: a unit quaternion and a
    translation in the parent's frame, both already converted from the
    12-bit and 7.9 fixed-point fields."""

    rotation: tuple[float, float, float, float]  # x, y, z, w
    translation: tuple[float, float, float]


@dataclass
class DseFile:
    kind: int  # DseKind bits
    raw_kind: int
    build_id: str
    name: str
    bones: list[Bone]
    meshes: list[Mesh]
    materials: list[Material]
    textures: list[Texture]
    animations: list[Animation]
    frame_count: int
    counts: tuple[int, ...]
    _frames: bytes = field(repr=False, default=b"")

    @property
    def is_model(self) -> bool:
        return bool(self.kind & DseKind.MODEL) and bool(self.meshes)

    @property
    def is_motion(self) -> bool:
        return (
            bool(self.kind & (DseKind.MOTION | DseKind.MOTION_SET))
            and self.frame_count > 0
        )

    @property
    def kind_name(self) -> str:
        names = [
            k.name.lower()
            for k in DseKind
            if self.kind & k and k is not DseKind.PATCHED
        ]
        return "+".join(names) or f"{self.raw_kind:#x}"

    @cached_property
    def bone_by_name(self) -> dict[str, Bone]:
        return {b.name: b for b in self.bones}

    @cached_property
    def bone_by_hash(self) -> dict[int, Bone]:
        return {b.name_hash: b for b in self.bones}

    def children(self, bone: int) -> list[Bone]:
        return [b for b in self.bones if b.parent == bone]

    def pose(self, frame: int, bone: int) -> Pose:
        """The local pose of ``bone`` in ``frame``."""
        if not 0 <= frame < self.frame_count:
            raise IndexError(f"frame {frame} of {self.frame_count}")
        offset = (frame * len(self.bones) + bone) * POSE_SIZE
        return decode_pose(self._frames[offset : offset + POSE_SIZE])

    def frame(self, frame: int) -> list[Pose]:
        return [self.pose(frame, b) for b in range(len(self.bones))]

    @property
    def groups(self) -> list[int]:
        return sorted({m.group for m in self.meshes})

    @property
    def parts(self) -> list[int]:
        return sorted({m.part for m in self.materials})


def _s12(v: int) -> int:
    v &= 0xFFF
    return v - 0x1000 if v & 0x800 else v


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def decode_pose(record: bytes) -> Pose:
    """Decode a 12-byte pose record.

    Two words hold the quaternion as four signed 12-bit fields (``1.0`` is
    ``0x800``, so the identity's ``w`` reads as -1, which is the same
    rotation): ``qy`` and ``qx`` in the top 24 bits of the first, ``qw`` and
    ``qz`` in the top 24 bits of the second. Their low bytes are the high and
    low halves of the z translation. The third word is ``ty << 16 | tx``. All
    three translations are 7.9 fixed point.
    """
    w0, w1, w2 = struct.unpack("<3I", record)
    qy = _s12(w0 >> 20) / 2048
    qx = _s12(w0 >> 8) / 2048
    qw = _s12(w1 >> 20) / 2048
    qz = _s12(w1 >> 8) / 2048
    tz = _s16(((w0 & 0xFF) << 8) | (w1 & 0xFF)) / 512
    tx = _s16(w2 & 0xFFFF) / 512
    ty = _s16(w2 >> 16) / 512
    return Pose((qx, qy, qz, qw), (tx, ty, tz))


def _unpack_texels(region: bytes, unpacked: int) -> bytes:
    """Texel data is stored raw, as BIOS LZ77, or as Okumura LZSS behind a
    4-byte unpacked-size word; the first bytes tell which."""
    if len(region) == unpacked:
        return region
    if len(region) < 4:
        return b""
    header = struct.unpack_from("<I", region)[0]
    if region[0] == 0x10 and header >> 8 == unpacked:
        return lz77_decompress(region)
    if header == unpacked:
        return lzss_decompress(region[4:], unpacked)
    return b""


def is_dse(data: bytes) -> bool:
    return len(data) >= HEADER_SIZE and data[:4] == MAGIC


def parse(data: bytes) -> DseFile:
    """Parse a whole DSE file held in ``data``."""
    if not is_dse(data):
        raise DseError("not a DSE file")
    raw_kind = data[6]
    kind = raw_kind & ~DseKind.PATCHED
    build_id = data[0x0A:0x1E].split(b"\0", 1)[0].decode("ascii", "replace")
    counts = struct.unpack_from("<9H", data, 0x1E)
    n_bones, n_meshes, _n_lists, n_materials, n_textures = counts[2:7]
    n_animations = counts[8]
    table = struct.unpack_from("<13I", data, 0x30)
    rel = [HEADER_SIZE + t for t in table]
    if table[12] > len(data):
        raise DseError("section table points past the end of the file")

    strings_off, strings_size = rel[7], table[8]
    strings = data[strings_off : strings_off + strings_size]

    def string(offset: int) -> str:
        if offset >= len(strings):
            return ""
        end = strings.find(b"\0", offset)
        if end < 0:
            end = len(strings)
        return strings[offset:end].decode("ascii", "replace")

    # -- bones ----------------------------------------------------------------
    bones: list[Bone] = []
    bones_off = HEADER_SIZE + table[0]
    for i in range(n_bones):
        p = bones_off + _BONE_SIZE * i
        (
            _one,
            flags,
            name_hash,
            unk_a,
            unk_b,
            name,
            unk_c,
            parent,
            unk_d,
            matrix_off,
        ) = struct.unpack_from("<BBHHHHHhHI", data, p)
        m = struct.unpack_from("<12i", data, HEADER_SIZE + matrix_off)
        bones.append(
            Bone(
                index=i,
                name=string(name),
                name_hash=name_hash,
                parent=parent,
                flags=flags,
                inverse_bind=Matrix4x3(tuple(v / 4096 for v in m)),
                unknown_a=unk_b,
                unknown_b=unk_d,
                unknown_c=unk_c | (unk_a << 16),
            )
        )

    # -- meshes ---------------------------------------------------------------
    meshes: list[Mesh] = []
    chunk_offsets = sorted(
        struct.unpack_from("<I", data, rel[1] + _MESH_SIZE * i + 4)[0]
        for i in range(n_meshes)
    )
    for i in range(n_meshes):
        name_off, chunk_off, flags, color = struct.unpack_from(
            "<4I", data, rel[1] + _MESH_SIZE * i
        )
        _name2, bone, _dl_index, _zero = struct.unpack_from(
            "<4I", data, rel[2] + _MESH_SIZE * i
        )
        p = HEADER_SIZE + chunk_off
        chunk_type, chunk_flags, mat_word, header_len = struct.unpack_from(
            "<BBHI", data, p
        )
        if chunk_type == 1:
            # An empty mesh: its table entry points at an end chunk.
            chunk_flags, mat_word = 0, 0
        elif chunk_type != 2 or header_len != 8:
            raise DseError(f"mesh {i}: expected a mesh chunk at {p:#x}")
        lists: list[DisplayList] = []
        material, alpha = mat_word & 0x3FF, (mat_word >> 10) & 0x1F
        # A mesh runs to its end chunk (type 1), switching material at every
        # further type-2 chunk on the way. Not every mesh has an end chunk:
        # some run straight into the next mesh's table offset.
        stop = min(
            (HEADER_SIZE + o for o in chunk_offsets if HEADER_SIZE + o > p),
            default=rel[1],
        )
        while p + 8 <= stop and chunk_type != 1:
            ctype, _cflags, cvalue, size = struct.unpack_from("<BBHI", data, p)
            if ctype == 1:
                break
            if ctype == 2 and size == 8:
                material, alpha = cvalue & 0x3FF, (cvalue >> 10) & 0x1F
                p += size
                continue
            if ctype not in (3, 4) or size < 32 or p + size > stop:
                break
            count, layout = struct.unpack_from("<HH", data, p + 8)
            list_bones: tuple[int, ...] = ()
            header = 0
            if layout & 0x1000:
                # CPU-skinned: the header word is where the weights start in
                # each vertex, and the bones they refer to follow it.
                header = struct.unpack_from("<I", data, p + 12)[0]
                n_weights = max(0, ((layout & 0xFF) - header) // 2)
                list_bones = struct.unpack_from(f"<{n_weights}H", data, p + 16)
            lists.append(
                DisplayList(
                    Primitive(ctype),
                    count,
                    layout,
                    data[p + 32 : p + size],
                    list_bones,
                    header,
                    material,
                    alpha,
                )
            )
            p += size
        meshes.append(
            Mesh(
                index=i,
                name=string(name_off),
                bone=bone,
                material=mat_word & 0x3FF,
                alpha=(mat_word >> 10) & 0x1F,
                flags=flags & 0xFFFFFF,
                group=flags >> 24,
                color=(color & 0x1F, (color >> 5) & 0x1F, (color >> 10) & 0x1F),
                display_lists=tuple(lists),
                chunk_flags=chunk_flags,
                shift=(color >> 16) & 0xFF,
            )
        )

    # -- materials ------------------------------------------------------------
    materials: list[Material] = []
    for i in range(n_materials):
        p = rel[3] + _MATERIAL_SIZE * i
        raw = data[p : p + _MATERIAL_SIZE]
        name_off, flags, _alpha, part, diffuse = struct.unpack_from("<IHBBH", raw)
        tex = raw[0x10]
        # TEXIMAGE_PARAM layout: repeat S/T in bits 0-1, flip S/T in bits 2-3.
        # The material keeps repeat in bits 2-3 and flip in bits 8-9, and a
        # flip implies repeat on the hardware.
        flip = (flags >> 8) & 3
        repeat = ((flags >> 2) & 3) | flip
        materials.append(
            Material(
                index=i,
                name=string(name_off),
                texture=None if tex == NO_TEXTURE or tex >= n_textures else tex,
                part=part,
                wrap=repeat | (flip << 2),
                diffuse=(diffuse & 0x1F, (diffuse >> 5) & 0x1F, (diffuse >> 10) & 0x1F),
                raw=raw,
            )
        )

    # -- textures -------------------------------------------------------------
    textures: list[Texture] = []
    texdata, texend = table[11], table[12]
    records = [
        struct.unpack_from("<6I2H5I", data, rel[4] + _TEXTURE_SIZE * i)
        for i in range(n_textures)
    ]
    # The table's packed-size field is not reliable in every file, so a
    # texture's bytes run to whatever comes next in the data block.
    boundaries = sorted(
        {r[1] for r in records} | {r[4] for r in records} | {texend - texdata}
    )
    for i, rec in enumerate(records):
        path_off, data_off, unpacked, _packed, pal_off, pal_size, width, height = rec[
            :8
        ]
        fmt_word, name_off = rec[10], rec[11]
        fmt = texture.TextureFormat(fmt_word & 0xFF)
        pal_count = max(1, (fmt_word >> 24) & 0xFF)
        end = next((b for b in boundaries if b > data_off), texend - texdata)
        region = data[texdata + data_off : texdata + end]
        texels = b""
        if texdata and region:
            try:
                texels = _unpack_texels(region, unpacked)
            except CompressionError:
                texels = b""  # listed but unusable; the viewer shows it as missing
        palette = (
            data[texdata + pal_off : texdata + pal_off + pal_size] if texdata else b""
        )
        each = len(palette) // pal_count if pal_count else len(palette)
        palettes = tuple(palette[k * each : (k + 1) * each] for k in range(pal_count))
        textures.append(
            Texture(
                index=i,
                name=string(name_off) or string(path_off).rsplit("/", 1)[-1],
                path=string(path_off),
                width=width,
                height=height,
                format=fmt,
                palette_count=pal_count,
                texels=texels,
                palettes=palettes,
                raw_format=fmt_word,
            )
        )

    # -- animations -----------------------------------------------------------
    animations: list[Animation] = []
    if table[6]:
        for i in range(n_animations):
            p = rel[6] + _ANIMATION_SIZE * i
            name_off, start, unknown, first, last = struct.unpack_from(
                "<IHHHH", data, p
            )
            animations.append(
                Animation(i, string(name_off), start, first, last, unknown)
            )

    # -- frames ---------------------------------------------------------------
    frames = b""
    frame_count = 0
    if table[10] and n_bones:
        frames = data[table[10] : table[11]]
        frame_count = len(frames) // (POSE_SIZE * n_bones)

    return DseFile(
        kind=kind,
        raw_kind=raw_kind,
        build_id=build_id,
        name=string(table[9]),
        bones=bones,
        meshes=meshes,
        materials=materials,
        textures=textures,
        animations=animations,
        frame_count=frame_count,
        counts=counts,
        _frames=frames,
    )
