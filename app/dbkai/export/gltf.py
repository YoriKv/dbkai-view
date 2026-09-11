"""Write a :class:`~dbkai.model.scene.Model` as a binary glTF 2.0 file.

One ``.glb`` holds the meshes, the skeleton as a skin, every texture as an
embedded PNG, and any bound motion clips as animations sampled per frame. The
result opens in Blender, three.js, Godot and the other glTF importers.

Coordinate system: the game's models are Y-up and the exporter keeps them so,
which is what glTF expects. Units are the game's own.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from dbkai.export.png import encode_png
from dbkai.model import math3d
from dbkai.model.animation import BoundMotion, Clip
from dbkai.model.scene import MeshData, Model

#: The game runs at 60 frames per second; motions are keyed every frame.
FRAME_RATE = 60.0

_COMPONENT = {
    np.dtype(np.float32): 5126,
    np.dtype(np.uint16): 5123,
    np.dtype(np.uint32): 5125,
    np.dtype(np.uint8): 5121,
}
_TYPE = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}


@dataclass
class _Builder:
    buffer: bytearray = field(default_factory=bytearray)
    views: list[dict[str, Any]] = field(default_factory=list)
    accessors: list[dict[str, Any]] = field(default_factory=list)

    def view(
        self, data: bytes, target: int | None = None, stride: int | None = None
    ) -> int:
        while len(self.buffer) % 4:
            self.buffer.append(0)
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": len(self.buffer),
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        if stride is not None:
            view["byteStride"] = stride
        self.buffer.extend(data)
        self.views.append(view)
        return len(self.views) - 1

    def accessor(
        self,
        array: np.ndarray,
        target: int | None = None,
        minmax: bool = False,
        normalized: bool = False,
    ) -> int:
        array = np.ascontiguousarray(array)
        count = array.shape[0]
        width = 1 if array.ndim == 1 else int(np.prod(array.shape[1:]))
        view = self.view(array.tobytes(), target)
        acc: dict[str, Any] = {
            "bufferView": view,
            "componentType": _COMPONENT[array.dtype],
            "count": int(count),
            "type": _TYPE[width],
        }
        if normalized:
            acc["normalized"] = True
        if minmax:
            flat = array.reshape(count, width)
            acc["min"] = [float(v) for v in flat.min(axis=0)]
            acc["max"] = [float(v) for v in flat.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def export_glb(
    model: Model,
    visible: list[MeshData] | None = None,
    motions: list[tuple[BoundMotion, Clip]] | None = None,
    palette: int = 0,
    all_meshes_as_nodes: bool = True,
) -> bytes:
    """Build the ``.glb`` bytes.

    ``visible`` restricts the meshes written (default: all of them, each as
    its own node so a viewer can toggle parts). ``motions`` are ``(bound
    motion, clip)`` pairs to write as animations; the clip's frames become
    keyframes at :data:`FRAME_RATE`.
    """
    b = _Builder()
    meshes = visible if visible is not None else model.meshes
    skeleton = model.skeleton
    n_bones = len(skeleton)

    # -- textures and materials -----------------------------------------------
    images: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    samplers: list[dict[str, Any]] = []
    tex_index: dict[int, int] = {}
    for t in model.textures:
        if not t.available:
            continue
        rgba = t.rgba(min(palette, t.palette_count - 1))
        png = encode_png(rgba.width, rgba.height, rgba.pixels)
        images.append(
            {"bufferView": b.view(png), "mimeType": "image/png", "name": t.name}
        )
        tex_index[t.index] = len(images) - 1

    def sampler(repeat_s: bool, repeat_t: bool, flip_s: bool, flip_t: bool) -> int:
        def wrap(repeat: bool, flip: bool) -> int:
            return (
                33648 if flip else 10497 if repeat else 33071
            )  # mirrored/repeat/clamp

        key = {
            "magFilter": 9728,
            "minFilter": 9728,
            "wrapS": wrap(repeat_s, flip_s),
            "wrapT": wrap(repeat_t, flip_t),
        }
        if key not in samplers:
            samplers.append(key)
        return samplers.index(key)

    materials: list[dict[str, Any]] = []
    for m in model.materials:
        mat: dict[str, Any] = {
            "name": m.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1, 1, 1, m.alpha / 31],
                "metallicFactor": 0,
                "roughnessFactor": 1,
            },
            "extensions": {"KHR_materials_unlit": {}},
        }
        if m.texture is not None and m.texture in tex_index:
            textures.append(
                {
                    "sampler": sampler(m.repeat_s, m.repeat_t, m.flip_s, m.flip_t),
                    "source": tex_index[m.texture],
                }
            )
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": len(textures) - 1
            }
            src = model.textures[m.texture]
            if src.color0_transparent or src.format.name in ("A3I5", "A5I3"):
                mat["alphaMode"] = "MASK" if src.color0_transparent else "BLEND"
                mat["alphaCutoff"] = 0.5
        if m.alpha < 31:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)
    # Double-sided needs its own material instance.
    ds_material: dict[int, int] = {}

    def material_for(mesh: MeshData) -> int | None:
        if mesh.material >= len(materials):
            return None
        if not mesh.double_sided:
            return mesh.material
        if mesh.material not in ds_material:
            copy = json.loads(json.dumps(materials[mesh.material]))
            copy["doubleSided"] = True
            copy["name"] += "_2s"
            materials.append(copy)
            ds_material[mesh.material] = len(materials) - 1
        return ds_material[mesh.material]

    # -- skeleton -------------------------------------------------------------
    nodes: list[dict[str, Any]] = []
    bone_nodes: list[int] = []
    rest = skeleton.bind_pose()
    for i in range(n_bones):
        node: dict[str, Any] = {"name": skeleton.names[i]}
        t = rest[i].translation
        q = rest[i].rotation
        if any(abs(v) > 1e-9 for v in t):
            node["translation"] = [float(v) for v in t]
        if abs(q[3]) < 1 - 1e-9:
            node["rotation"] = [float(v) for v in q]
        nodes.append(node)
        bone_nodes.append(i)
    for i in range(n_bones):
        p = skeleton.parents[i]
        if p >= 0:
            nodes[p].setdefault("children", []).append(i)
    skin: dict[str, Any] | None = None
    if n_bones:
        ibm = np.array(
            [skeleton.inverse_bind[i].T for i in range(n_bones)], dtype=np.float32
        )
        skin = {
            "joints": bone_nodes,
            "inverseBindMatrices": b.accessor(ibm.reshape(n_bones, 16)),
        }

    # -- meshes ---------------------------------------------------------------
    gl_meshes: list[dict[str, Any]] = []
    mesh_nodes: list[int] = []
    for mesh in meshes:
        if mesh.vertex_count == 0 or mesh.face_count == 0:
            continue
        attrs = {
            "POSITION": b.accessor(
                mesh.positions.astype(np.float32), 34962, minmax=True
            ),
            "TEXCOORD_0": b.accessor(mesh.uvs.astype(np.float32), 34962),
            "COLOR_0": b.accessor(mesh.colors.astype(np.float32), 34962),
        }
        if skin is not None:
            k = mesh.joints.shape[1]
            for set_index in range((k + 3) // 4):
                j = np.zeros((mesh.vertex_count, 4), dtype=np.uint16)
                w = np.zeros((mesh.vertex_count, 4), dtype=np.float32)
                cols = min(4, k - 4 * set_index)
                j[:, :cols] = mesh.joints[:, 4 * set_index : 4 * set_index + cols]
                w[:, :cols] = mesh.weights[:, 4 * set_index : 4 * set_index + cols]
                attrs[f"JOINTS_{set_index}"] = b.accessor(j, 34962)
                attrs[f"WEIGHTS_{set_index}"] = b.accessor(w, 34962)
        prim: dict[str, Any] = {
            "attributes": attrs,
            "indices": b.accessor(mesh.indices.astype(np.uint32).reshape(-1), 34963),
            "mode": 4,
        }
        mat = material_for(mesh)
        if mat is not None:
            prim["material"] = mat
        gl_meshes.append({"name": mesh.name, "primitives": [prim]})
        node = {
            "name": mesh.name,
            "mesh": len(gl_meshes) - 1,
            "extras": {"group": mesh.group, "part": mesh.part},
        }
        if skin is not None:
            node["skin"] = 0
        nodes.append(node)
        mesh_nodes.append(len(nodes) - 1)

    roots = [i for i in range(n_bones) if skeleton.parents[i] < 0] + mesh_nodes

    # -- animations -----------------------------------------------------------
    animations: list[dict[str, Any]] = []
    for bound, clip in motions or []:
        if clip.frame_count <= 0:
            continue
        times = np.arange(clip.frame_count, dtype=np.float32) / FRAME_RATE
        time_acc = b.accessor(times, minmax=True)
        rot = np.zeros((clip.frame_count, n_bones, 4), dtype=np.float32)
        trans = np.zeros((clip.frame_count, n_bones, 3), dtype=np.float32)
        for k in range(clip.frame_count):
            poses = bound.local_poses(clip.start + k, rest)
            for i, p in enumerate(poses):
                q = np.array(p.rotation, dtype=np.float64)
                n = np.linalg.norm(q)
                rot[k, i] = q / n if n else (0, 0, 0, 1)
                trans[k, i] = p.translation
        # Keep quaternion continuity so importers interpolate the short way.
        for i in range(n_bones):
            for k in range(1, clip.frame_count):
                if np.dot(rot[k, i], rot[k - 1, i]) < 0:
                    rot[k, i] = -rot[k, i]
        channels = []
        samplers_a = []
        for i in range(n_bones):
            samplers_a.append(
                {
                    "input": time_acc,
                    "output": b.accessor(rot[:, i]),
                    "interpolation": "LINEAR",
                }
            )
            channels.append(
                {
                    "sampler": len(samplers_a) - 1,
                    "target": {"node": i, "path": "rotation"},
                }
            )
            samplers_a.append(
                {
                    "input": time_acc,
                    "output": b.accessor(trans[:, i]),
                    "interpolation": "LINEAR",
                }
            )
            channels.append(
                {
                    "sampler": len(samplers_a) - 1,
                    "target": {"node": i, "path": "translation"},
                }
            )
        animations.append(
            {"name": clip.name, "channels": channels, "samplers": samplers_a}
        )

    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "dbkai"},
        "scene": 0,
        "scenes": [{"name": model.name, "nodes": roots}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": materials,
        "buffers": [{"byteLength": 0}],
        "bufferViews": b.views,
        "accessors": b.accessors,
        "extensionsUsed": ["KHR_materials_unlit"],
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
        gltf["samplers"] = samplers
    if skin is not None:
        gltf["skins"] = [skin]
    if animations:
        gltf["animations"] = animations
    while len(b.buffer) % 4:
        b.buffer.append(0)
    gltf["buffers"][0]["byteLength"] = len(b.buffer)
    return _pack_glb(gltf, bytes(b.buffer))


def _pack_glb(gltf: dict[str, Any], binary: bytes) -> bytes:
    text = json.dumps(gltf, separators=(",", ":")).encode()
    while len(text) % 4:
        text += b" "
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def bone_local_from_world(model: Model, world: np.ndarray, bone: int) -> math3d.Mat4:
    """The local matrix of ``bone`` given every bone's world matrix."""
    p = model.skeleton.parents[bone]
    return world[bone] if p < 0 else np.linalg.inv(world[p]) @ world[bone]
