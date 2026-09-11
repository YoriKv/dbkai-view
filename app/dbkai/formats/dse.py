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

_BONE = struct.Struct("<BBHHHHHhHI")
_CHUNK = struct.Struct("<BBHI")  # type, flags, value, length (header included)
_LIST_HEADER_SIZE = 32
_TEXTURE = struct.Struct("<6I2H5I")
_ANIMATION = struct.Struct("<IHHHH")
# A CPU-skinned vertex up to its weights: s, t, [RGB555,] x, y, z.
_SKINNED = struct.Struct("<2h3h")
_SKINNED_COLOR = struct.Struct("<2hH3h")

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
    unknown_a: int  # record 0x06: 1, or the frame count in motion files
    mirror: int  # the bone drawn instead when the character is mirrored
    unknown_c: int  # record 0x0A, and 0x04 in the high half; 0 in the ROM

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
    GPU; :meth:`vertices` decodes those. A list whose layout has ``0x1000``
    set is instead a raw vertex array the game skins on the CPU against
    ``bones``;
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
        """Decode a raw vertex array: the 12.4 texture coordinate, an RGB555
        colour word when layout ``0x0400`` is set, the position (three 4.12
        words), then one 4.12 weight per bone from the byte offset
        ``header``."""
        if not self.is_skinned:
            raise DseError("not a CPU-skinned list")
        stride = self.stride
        has_color = bool(self.layout & 0x400)
        head = _SKINNED_COLOR if has_color else _SKINNED
        weights = struct.Struct(f"<{len(self.bones)}H")
        if self.header < head.size or stride != self.header + weights.size:
            raise DseError(
                f"skinned list stride {stride} does not fit {len(self.bones)} "
                f"bone weights from offset {self.header}"
            )
        if self.vertex_count * stride > len(self.data):
            raise DseError(
                f"skinned list holds {len(self.data)} bytes, "
                f"{self.vertex_count} vertices need {self.vertex_count * stride}"
            )
        out: list[SkinnedVertex] = []
        for at in range(0, self.vertex_count * stride, stride):
            f = head.unpack_from(self.data, at)
            color = gx.rgb555(f[2]) if has_color else None
            x, y, z = f[-3:]
            out.append(
                SkinnedVertex(
                    (x / 4096, y / 4096, z / 4096),
                    (f[0] / 16, f[1] / 16),
                    color,
                    tuple(
                        w / 4096
                        for w in weights.unpack_from(self.data, at + self.header)
                    ),
                )
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

    @property
    def color0_transparent(self) -> bool:
        """Colour 0 of a palette is transparent: bit 0 of record byte
        ``0x26`` is clear."""
        return not (self.raw_format >> 16) & 1

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
        if not 0 <= bone < len(self.bones):
            raise IndexError(f"bone {bone} of {len(self.bones)}")
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
    qy = gx.signed(w0 >> 20, 12) / 2048
    qx = gx.signed(w0 >> 8, 12) / 2048
    qw = gx.signed(w1 >> 20, 12) / 2048
    qz = gx.signed(w1 >> 8, 12) / 2048
    tz = gx.signed(((w0 & 0xFF) << 8) | (w1 & 0xFF), 16) / 512
    tx = gx.signed(w2, 16) / 512
    ty = gx.signed(w2 >> 16, 16) / 512
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
    """Whether ``data`` starts like a DSE file."""
    return len(data) >= HEADER_SIZE and data[:4] == MAGIC


def parse(data: bytes) -> DseFile:
    """Parse a whole DSE file held in ``data``."""
    if not is_dse(data):
        raise DseError("not a DSE file")
    try:
        return _parse(data)
    except struct.error as exc:
        raise DseError(f"a table runs past the end of the file: {exc}") from exc


def _parse(data: bytes) -> DseFile:
    raw_kind = data[6]
    build_id = data[0x0A:0x1E].split(b"\0", 1)[0].decode("ascii", "replace")
    counts = struct.unpack_from("<9H", data, 0x1E)
    n_bones, n_meshes, _n_lists, n_materials, n_textures = counts[2:7]
    n_animations = counts[8]
    table = struct.unpack_from("<13I", data, 0x30)
    rel = [HEADER_SIZE + t for t in table]
    if table[12] > len(data):
        raise DseError("section table points past the end of the file")
    strings = data[rel[7] : rel[7] + table[8]]

    frames = b""
    frame_count = 0
    if table[10] and n_bones:
        frames = data[table[10] : table[11]]
        frame_count = len(frames) // (POSE_SIZE * n_bones)

    return DseFile(
        kind=raw_kind & ~DseKind.PATCHED,
        raw_kind=raw_kind,
        build_id=build_id,
        name=_string(strings, table[9]),
        bones=_bones(data, rel[0], n_bones, strings),
        meshes=_meshes(data, rel[1], rel[2], n_meshes, strings),
        materials=_materials(data, rel[3], n_materials, n_textures, strings),
        textures=_textures(data, rel[4], n_textures, table[11], table[12], strings),
        animations=_animations(data, rel[6], n_animations, strings) if table[6] else [],
        frame_count=frame_count,
        counts=counts,
        _frames=frames,
    )


def _string(strings: bytes, offset: int) -> str:
    """The NUL-terminated string at ``offset`` of the string table."""
    if offset >= len(strings):
        return ""
    end = strings.find(b"\0", offset)
    if end < 0:
        end = len(strings)
    return strings[offset:end].decode("ascii", "replace")


def _bones(data: bytes, at: int, count: int, strings: bytes) -> list[Bone]:
    bones: list[Bone] = []
    for i in range(count):
        (
            _kind,
            flags,
            name_hash,
            unk_a,
            unk_b,
            name,
            unk_c,
            parent,
            mirror,
            matrix_off,
        ) = _BONE.unpack_from(data, at + _BONE_SIZE * i)
        m = struct.unpack_from("<12i", data, HEADER_SIZE + matrix_off)
        bones.append(
            Bone(
                index=i,
                name=_string(strings, name),
                name_hash=name_hash,
                parent=parent,
                flags=flags,
                inverse_bind=Matrix4x3(tuple(v / 4096 for v in m)),
                unknown_a=unk_b,
                mirror=mirror,
                unknown_c=unk_c | (unk_a << 16),
            )
        )
    return bones


def _material_select(value: int) -> tuple[int, int]:
    """(material, alpha) of a material-select chunk's value. The draw
    routine reads only the low byte; the alpha the tool wrote at bit 10 is
    kept for :attr:`Mesh.alpha` but never drawn with."""
    return value & 0xFF, (value >> 10) & 0x1F


def _meshes(
    data: bytes, table_a: int, table_b: int, count: int, strings: bytes
) -> list[Mesh]:
    # A mesh without an end chunk runs into the next mesh's chunk, so every
    # mesh's start bounds the one before it; the last runs to table A.
    starts = sorted(
        HEADER_SIZE + struct.unpack_from("<I", data, table_a + _MESH_SIZE * i + 4)[0]
        for i in range(count)
    )
    meshes: list[Mesh] = []
    for i in range(count):
        name_off, chunk_off, flags, color = struct.unpack_from(
            "<4I", data, table_a + _MESH_SIZE * i
        )
        _name2, bone, _dl_index, _zero = struct.unpack_from(
            "<4I", data, table_b + _MESH_SIZE * i
        )
        p = HEADER_SIZE + chunk_off
        chunk_type, chunk_flags, value, length = _CHUNK.unpack_from(data, p)
        if chunk_type == 1:
            # An empty mesh: its table entry points at an end chunk.
            chunk_flags, value = 0, 0
        elif chunk_type != 2 or length != 8:
            raise DseError(f"mesh {i}: expected a mesh chunk at {p:#x}")
        stop = next((s for s in starts if s > p), table_a)
        material, alpha = _material_select(value)
        meshes.append(
            Mesh(
                index=i,
                name=_string(strings, name_off),
                bone=bone,
                material=material,
                alpha=alpha,
                flags=flags & 0xFFFFFF,
                group=flags >> 24,
                color=gx.rgb555(color),
                display_lists=_display_lists(data, p, stop),
                chunk_flags=chunk_flags,
                shift=(color >> 16) & 0xFF,
            )
        )
    return meshes


def _display_lists(data: bytes, p: int, stop: int) -> tuple[DisplayList, ...]:
    """The display lists of the mesh whose chunks start at ``p``.

    A mesh runs to its end chunk (type 1) or to ``stop``, switching material
    at every material-select chunk (type 2) on the way.
    """
    lists: list[DisplayList] = []
    material = alpha = 0
    while p + _CHUNK.size <= stop:
        ctype, _cflags, value, size = _CHUNK.unpack_from(data, p)
        if ctype == 1:
            break
        if ctype == 2 and size == 8:
            material, alpha = _material_select(value)
            p += size
            continue
        if ctype not in (3, 4) or size < _LIST_HEADER_SIZE or p + size > stop:
            break
        count, layout = struct.unpack_from("<HH", data, p + 8)
        bones: tuple[int, ...] = ()
        header = 0
        if layout & 0x1000:
            # CPU-skinned: the header word is where the weights start in
            # each vertex, and the bones they refer to follow it.
            header = struct.unpack_from("<I", data, p + 12)[0]
            n_weights = max(0, ((layout & 0xFF) - header) // 2)
            bones = struct.unpack_from(f"<{n_weights}H", data, p + 16)
        lists.append(
            DisplayList(
                Primitive(ctype),
                count,
                layout,
                data[p + _LIST_HEADER_SIZE : p + size],
                bones,
                header,
                material,
                alpha,
            )
        )
        p += size
    return tuple(lists)


def _materials(
    data: bytes, at: int, count: int, n_textures: int, strings: bytes
) -> list[Material]:
    materials: list[Material] = []
    for i in range(count):
        p = at + _MATERIAL_SIZE * i
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
                name=_string(strings, name_off),
                texture=None if tex == NO_TEXTURE or tex >= n_textures else tex,
                part=part,
                wrap=repeat | (flip << 2),
                diffuse=gx.rgb555(diffuse),
                raw=raw,
            )
        )
    return materials


def _textures(
    data: bytes, at: int, count: int, texdata: int, texend: int, strings: bytes
) -> list[Texture]:
    """The texture table; ``texdata`` and ``texend`` bound the texel and
    palette block (file offsets, ``texdata`` 0 when there is none)."""
    records = [_TEXTURE.unpack_from(data, at + _TEXTURE_SIZE * i) for i in range(count)]
    # The table's packed-size field is not reliable in every file, so a
    # texture's bytes run to whatever comes next in the data block.
    block_size = texend - texdata
    boundaries = sorted(
        {r[1] for r in records} | {r[4] for r in records} | {block_size}
    )
    textures: list[Texture] = []
    for i, rec in enumerate(records):
        path_off, data_off, unpacked, _packed, pal_off, pal_size, width, height = rec[
            :8
        ]
        fmt_word, name_off = rec[10], rec[11]
        try:
            fmt = texture.TextureFormat(fmt_word & 0xFF)
        except ValueError:
            raise DseError(f"texture {i}: unknown format {fmt_word & 0xFF}") from None
        pal_count = max(1, (fmt_word >> 24) & 0xFF)
        end = next((b for b in boundaries if b > data_off), block_size)
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
        each = len(palette) // pal_count
        palettes = tuple(palette[k * each : (k + 1) * each] for k in range(pal_count))
        path = _string(strings, path_off)
        textures.append(
            Texture(
                index=i,
                name=_string(strings, name_off) or path.rsplit("/", 1)[-1],
                path=path,
                width=width,
                height=height,
                format=fmt,
                palette_count=pal_count,
                texels=texels,
                palettes=palettes,
                raw_format=fmt_word,
            )
        )
    return textures


def _animations(data: bytes, at: int, count: int, strings: bytes) -> list[Animation]:
    animations: list[Animation] = []
    for i in range(count):
        name_off, start, unknown, first, last = _ANIMATION.unpack_from(
            data, at + _ANIMATION_SIZE * i
        )
        animations.append(
            Animation(i, _string(strings, name_off), start, first, last, unknown)
        )
    return animations
