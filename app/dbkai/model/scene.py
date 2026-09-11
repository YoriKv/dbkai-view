"""A model as the game draws it: render-ready meshes over a skeleton.

:func:`build` takes a parsed model file and produces :class:`Model`. Every
mesh ends up as triangles with model-space (bind pose) positions, texture
coordinates in 0..1 units of the texture, per-vertex colours, and joint
weights, whatever path the game uses for it:

- A hardware list stores bone-local vertices, DMA'd behind the bone's world
  matrix. They are moved to model space through the bone's bind matrix and
  weighted 100% to that bone.
- A CPU-skinned list stores model-space vertices with up to seven weights,
  which are kept as they are.

Both kinds may be stored divided by a power of two (the mesh's ``shift``), which
the game undoes with a scale; that is undone here too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dbkai.formats import dse, texture
from dbkai.model import math3d
from dbkai.model.skeleton import Skeleton

#: How many texture-coordinate units span a texture: the game's texture matrix
#: scales raw coordinates by ``width / 256``.
UV_UNITS = 256.0


@dataclass
class MeshData:
    """One drawable mesh."""

    index: int
    name: str
    material: int
    group: int
    part: int
    double_sided: bool
    skinned: bool
    bone: int
    positions: np.ndarray  # (V, 3) float32, model space, bind pose
    uvs: np.ndarray  # (V, 2) float32, 0..1 = one texture
    colors: np.ndarray  # (V, 3) float32, 0..1
    joints: np.ndarray  # (V, K) int32
    weights: np.ndarray  # (V, K) float32
    indices: np.ndarray  # (F, 3) uint32
    has_vertex_colors: bool
    fog: bool
    alpha: int
    shift: int

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def face_count(self) -> int:
        return len(self.indices)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.positions.min(axis=0), self.positions.max(axis=0)


@dataclass
class MaterialData:
    index: int
    name: str
    texture: int | None
    part: int
    repeat_s: bool
    repeat_t: bool
    flip_s: bool
    flip_t: bool
    alpha: int
    diffuse: tuple[float, float, float]


@dataclass
class TextureData:
    index: int
    name: str
    width: int
    height: int
    format: texture.TextureFormat
    palette_count: int
    color0_transparent: bool
    available: bool
    _source: dse.Texture = field(repr=False)

    def rgba(self, palette: int = 0) -> texture.Rgba:
        return self._source.decode(palette, self.color0_transparent)


@dataclass
class Model:
    name: str
    skeleton: Skeleton
    meshes: list[MeshData]
    materials: list[MaterialData]
    textures: list[TextureData]
    source: dse.DseFile = field(repr=False)

    @property
    def groups(self) -> list[int]:
        return sorted({m.group for m in self.meshes})

    @property
    def parts(self) -> list[int]:
        return sorted({m.part for m in self.meshes})

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.meshes:
            return np.zeros(3), np.zeros(3)
        lo = np.min(
            [m.positions.min(axis=0) for m in self.meshes if len(m.positions)], axis=0
        )
        hi = np.max(
            [m.positions.max(axis=0) for m in self.meshes if len(m.positions)], axis=0
        )
        return lo, hi

    def default_visibility(self) -> tuple[set[int], set[int]]:
        """The game's usual selection: the (groups, parts) to show.

        Groups 0-5 are the body, face, hair and limbs; 6 up are alternatives
        the game switches between, of which the open hands (8 and 9) are the
        common rest state. Part 0 is always on; 1 is the neutral face and 5 the
        neutral mouth, and the rest are expressions and extras the game turns
        on by state.
        """
        groups = {g for g in self.groups if g <= 5 or g in (8, 9)}
        parts = {p for p in self.parts if p in (0, 1, 5)}
        return groups, parts

    def visible_meshes(self, groups: set[int], parts: set[int]) -> list[MeshData]:
        return [m for m in self.meshes if m.group in groups and m.part in parts]


def build(file: dse.DseFile, name: str = "") -> Model:
    """Turn a parsed model file into a :class:`Model`."""
    skeleton = Skeleton.from_dse(file)
    materials = [
        MaterialData(
            index=m.index,
            name=m.name,
            texture=m.texture,
            part=m.part,
            repeat_s=m.repeat_s,
            repeat_t=m.repeat_t,
            flip_s=m.flip_s,
            flip_t=m.flip_t,
            alpha=m.raw[6] if len(m.raw) > 6 else 31,
            diffuse=tuple(c / 31 for c in m.diffuse),  # type: ignore[arg-type]
        )
        for m in file.materials
    ]
    textures = [
        TextureData(
            index=t.index,
            name=t.name,
            width=t.width,
            height=t.height,
            format=t.format,
            palette_count=t.palette_count,
            color0_transparent=not (t.raw_format >> 16) & 1,
            available=t.has_data,
            _source=t,
        )
        for t in file.textures
    ]
    meshes = [_build_mesh(m, file, skeleton) for m in file.meshes]
    return Model(name or file.name, skeleton, meshes, materials, textures, file)


def _build_mesh(mesh: dse.Mesh, file: dse.DseFile, skeleton: Skeleton) -> MeshData:
    material = (
        file.materials[mesh.material] if mesh.material < len(file.materials) else None
    )
    part = material.part if material else 0
    default_color = np.array([c / 31 for c in mesh.color], dtype=np.float32)
    factor = float(1 << mesh.shift)
    positions: list[np.ndarray] = []
    uvs: list[np.ndarray] = []
    colors: list[np.ndarray] = []
    joints: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    indices: list[np.ndarray] = []
    max_joints = 1
    skinned = any(dl.is_skinned for dl in mesh.display_lists)
    for dl in mesh.display_lists:
        if dl.is_skinned:
            max_joints = max(max_joints, len(dl.bones))
    base = 0
    bone = mesh.bone if mesh.bone < len(skeleton) else 0
    bind = skeleton.bind_world[bone] if len(skeleton) else np.eye(4)
    for dl in mesh.display_lists:
        per = dl.primitive.vertices_per_face
        if dl.is_skinned:
            verts = dl.skinned_vertices()
            n = len(verts) - len(verts) % per
            if n == 0:
                continue
            pos = np.array([v.position for v in verts[:n]], dtype=np.float64) * factor
            uv = np.array([v.uv for v in verts[:n]], dtype=np.float32) / UV_UNITS
            col = np.array(
                [
                    [c / 31 for c in v.color] if v.color is not None else default_color
                    for v in verts[:n]
                ],
                dtype=np.float32,
            )
            j = np.zeros((n, max_joints), dtype=np.int32)
            w = np.zeros((n, max_joints), dtype=np.float32)
            j[:, : len(dl.bones)] = np.array(dl.bones, dtype=np.int32)
            w[:, : len(dl.bones)] = np.array(
                [v.weights for v in verts[:n]], dtype=np.float32
            )
        else:
            verts = dl.vertices()
            n = len(verts) - len(verts) % per
            if n == 0:
                continue
            local = np.array([v.position for v in verts[:n]], dtype=np.float64) * factor
            pos = math3d.transform_points(bind, local)
            uv = (
                np.array(
                    [v.uv if v.uv is not None else (0.0, 0.0) for v in verts[:n]],
                    dtype=np.float32,
                )
                / UV_UNITS
            )
            col = np.array(
                [
                    [c / 31 for c in v.color] if v.color is not None else default_color
                    for v in verts[:n]
                ],
                dtype=np.float32,
            )
            j = np.full((n, max_joints), bone, dtype=np.int32)
            w = np.zeros((n, max_joints), dtype=np.float32)
            w[:, 0] = 1.0
        faces = n // per
        if per == 3:
            idx = np.arange(n, dtype=np.uint32).reshape(faces, 3)
        else:
            q = np.arange(n, dtype=np.uint32).reshape(faces, 4)
            idx = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]], axis=0)
        positions.append(pos)
        uvs.append(uv)
        colors.append(col)
        joints.append(j)
        weights.append(w)
        indices.append(idx + base)
        base += n

    def cat(parts: list[np.ndarray], shape: tuple[int, ...], dtype: type) -> np.ndarray:
        return np.concatenate(parts, axis=0) if parts else np.zeros(shape, dtype=dtype)

    return MeshData(
        index=mesh.index,
        name=mesh.name,
        material=mesh.material,
        group=mesh.group,
        part=part,
        double_sided=mesh.double_sided,
        skinned=skinned,
        bone=bone,
        positions=cat(positions, (0, 3), np.float64).astype(np.float32),
        uvs=cat(uvs, (0, 2), np.float32),
        colors=cat(colors, (0, 3), np.float32),
        joints=cat(joints, (0, max_joints), np.int32),
        weights=cat(weights, (0, max_joints), np.float32),
        indices=cat(indices, (0, 3), np.uint32),
        has_vertex_colors=mesh.has_vertex_colors,
        fog=bool(mesh.flags & 0x20),
        alpha=mesh.alpha,
        shift=mesh.shift,
    )


def skin(mesh: MeshData, skin_matrices: np.ndarray) -> np.ndarray:
    """``mesh``'s positions under ``skin_matrices`` (``(N, 4, 4)``, one per
    bone): the sum of each weighted bone's transform."""
    out = np.zeros_like(mesh.positions, dtype=np.float64)
    pos = mesh.positions.astype(np.float64)
    for k in range(mesh.joints.shape[1]):
        j = mesh.joints[:, k]
        w = mesh.weights[:, k].astype(np.float64)
        if not np.any(w):
            continue
        m = skin_matrices[j]  # (V, 4, 4)
        moved = np.einsum("vij,vj->vi", m[:, :3, :3], pos) + m[:, :3, 3]
        out += moved * w[:, None]
    return out.astype(np.float32)
