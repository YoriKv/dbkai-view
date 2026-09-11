"""Write a :class:`~dbkai.model.scene.Model` as a binary glTF 2.0 file.

One ``.glb`` holds the meshes, the skeleton as a skin, every texture as an
embedded PNG, and any bound motion clips or actions as animations sampled
per frame. The result opens in Blender, three.js, Godot and the other glTF
importers.

An action also switches parts on and off. glTF ignores the transform of a
skinned mesh's node, so a part cannot be hidden by scaling it; the switches
are written as ``KHR_node_visibility`` values keyed through
``KHR_animation_pointer`` with step interpolation. An importer without those
extensions shows every part throughout.

Coordinate system: the game's models are Y-up and the exporter keeps them so,
which is what glTF expects. Units are the game's own.

The output passes the Khronos glTF validator. That takes a few departures
from the game's data: a skeleton with several root bones hangs under one
empty node, since a skin's joints need a common root; a vertex's weights are
scaled to sum to one, where the game's fixed-point ones fall a little short;
and a slot with no weight points at joint 0.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from dbkai.export.png import encode_png
from dbkai.model import math3d
from dbkai.model.animation import BoundMotion, Clip, Take
from dbkai.model.scene import MaterialData, MeshData, Model

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
    takes: list[Take] | None = None,
) -> bytes:
    """Build the ``.glb`` bytes.

    ``visible`` restricts the meshes written (default: all of them, each as
    its own node so a viewer can toggle parts). ``motions`` are ``(bound
    motion, clip)`` pairs to write as animations; the clip's frames become
    keyframes at :data:`FRAME_RATE`. ``takes`` are further animations, such
    as actions, written after the clips; a take's masks key the visibility
    of every mesh written.
    """
    b = _Builder()
    meshes = visible if visible is not None else model.meshes
    skeleton = model.skeleton
    n_bones = len(skeleton)

    # -- textures and materials -----------------------------------------------
    # Each is written on first use, so the file carries only what its meshes
    # draw with.
    images: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    samplers: list[dict[str, Any]] = []
    image_index: dict[int, int | None] = {}

    def image(index: int) -> int | None:
        if index not in image_index:
            t = model.textures[index]
            if not t.available:
                image_index[index] = None
            else:
                rgba = t.rgba(min(palette, t.palette_count - 1))
                png = encode_png(rgba.width, rgba.height, rgba.pixels)
                images.append(
                    {"bufferView": b.view(png), "mimeType": "image/png", "name": t.name}
                )
                image_index[index] = len(images) - 1
        return image_index[index]

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

    def texture(m: MaterialData) -> int | None:
        source = image(m.texture) if m.texture is not None else None
        if source is None:
            return None
        entry = {
            "sampler": sampler(m.repeat_s, m.repeat_t, m.flip_s, m.flip_t),
            "source": source,
        }
        if entry not in textures:
            textures.append(entry)
        return textures.index(entry)

    materials: list[dict[str, Any]] = []
    material_index: dict[tuple[int, bool], int] = {}

    def material_for(mesh: MeshData) -> int | None:
        """The mesh's material; a double-sided mesh gets its own instance."""
        if mesh.material >= len(model.materials):
            return None
        key = (mesh.material, mesh.double_sided)
        if key in material_index:
            return material_index[key]
        m = model.materials[mesh.material]
        mat: dict[str, Any] = {
            "name": m.name + ("_2s" if mesh.double_sided else ""),
            "pbrMetallicRoughness": {
                "baseColorFactor": [1, 1, 1, m.alpha / 31],
                "metallicFactor": 0,
                "roughnessFactor": 1,
            },
            "extensions": {"KHR_materials_unlit": {}},
        }
        if mesh.double_sided:
            mat["doubleSided"] = True
        tex = texture(m)
        if tex is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex}
            src = model.textures[m.texture]
            if src.color0_transparent or src.format.name in ("A3I5", "A5I3"):
                mat["alphaMode"] = "MASK" if src.color0_transparent else "BLEND"
        if m.alpha < 31:
            mat["alphaMode"] = "BLEND"
        if mat.get("alphaMode") == "MASK":
            mat["alphaCutoff"] = 0.5
        materials.append(mat)
        material_index[key] = len(materials) - 1
        return material_index[key]

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
    bone_roots = [i for i in range(n_bones) if skeleton.parents[i] < 0]
    skin: dict[str, Any] | None = None
    if n_bones:
        ibm = np.array(
            [skeleton.inverse_bind[i].T for i in range(n_bones)], dtype=np.float32
        )
        skin = {
            "joints": bone_nodes,
            "inverseBindMatrices": b.accessor(ibm.reshape(n_bones, 16)),
        }
        if len(bone_roots) > 1:
            nodes.append({"name": "skeleton", "children": bone_roots})
            bone_roots = [len(nodes) - 1]
            skin["skeleton"] = bone_roots[0]

    # -- meshes ---------------------------------------------------------------
    gl_meshes: list[dict[str, Any]] = []
    mesh_nodes: list[int] = []
    written: list[tuple[MeshData, int]] = []
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
            weights, joints = _normalized_weights(mesh.weights, mesh.joints)
            for set_index in range((k + 3) // 4):
                j = np.zeros((mesh.vertex_count, 4), dtype=np.uint16)
                w = np.zeros((mesh.vertex_count, 4), dtype=np.float32)
                cols = min(4, k - 4 * set_index)
                j[:, :cols] = joints[:, 4 * set_index : 4 * set_index + cols]
                w[:, :cols] = weights[:, 4 * set_index : 4 * set_index + cols]
                attrs[f"JOINTS_{set_index}"] = b.accessor(j, 34962)
                attrs[f"WEIGHTS_{set_index}"] = b.accessor(w, 34962)
        # A mesh the game draws back-faces-only is wound the other way round.
        indices = mesh.indices[:, ::-1] if mesh.inverted else mesh.indices
        prim: dict[str, Any] = {
            "attributes": attrs,
            "indices": b.accessor(indices.astype(np.uint32).reshape(-1), 34963),
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
        written.append((mesh, len(nodes) - 1))

    roots = bone_roots + mesh_nodes

    # -- animations -----------------------------------------------------------
    animations: list[dict[str, Any]] = []
    shown: dict[int, tuple[set[int], set[int]]] = {}
    switches = False
    every_take = [Take.of_clip(bound, clip) for bound, clip in motions or []]
    for take in every_take + list(takes or []):
        count = len(take.poses)
        if count <= 0:
            continue
        times = np.arange(count, dtype=np.float32) / FRAME_RATE
        time_acc = b.accessor(times, minmax=True)
        rot = np.zeros((count, n_bones, 4), dtype=np.float32)
        trans = np.zeros((count, n_bones, 3), dtype=np.float32)
        for k, posed in enumerate(take.poses):
            poses = rest if posed is None else posed[0].local_poses(posed[1], rest)
            for i, p in enumerate(poses):
                q = np.array(p.rotation, dtype=np.float64)
                n = np.linalg.norm(q)
                rot[k, i] = q / n if n else (0, 0, 0, 1)
                trans[k, i] = p.translation
        # Keep quaternion continuity so importers interpolate the short way.
        for i in range(n_bones):
            for k in range(1, count):
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
        for mesh, node_index in written if take.masks is not None else []:
            visible = np.zeros(count, dtype=np.uint8)
            for k, mask in enumerate(take.masks or []):
                if mask not in shown:
                    shown[mask] = model.visibility_from_mask(mask)
                groups, parts = shown[mask]
                visible[k] = mesh.group in groups and mesh.part in parts
            keys = [0] + [k for k in range(1, count) if visible[k] != visible[k - 1]]
            # The node's own value is what the first take shows at its start.
            nodes[node_index].setdefault("extensions", {}).setdefault(
                "KHR_node_visibility", {"visible": bool(visible[0])}
            )
            samplers_a.append(
                {
                    "input": b.accessor(times[keys], minmax=True),
                    "output": b.accessor(visible[keys]),
                    "interpolation": "STEP",
                }
            )
            pointer = f"/nodes/{node_index}/extensions/KHR_node_visibility/visible"
            channels.append(
                {
                    "sampler": len(samplers_a) - 1,
                    "target": {
                        "path": "pointer",
                        "extensions": {"KHR_animation_pointer": {"pointer": pointer}},
                    },
                }
            )
            switches = True
        animations.append(
            {"name": take.name, "channels": channels, "samplers": samplers_a}
        )

    scene: dict[str, Any] = {"name": model.name}
    if roots:
        scene["nodes"] = roots
    extensions = ["KHR_materials_unlit"] if materials else []
    if switches:
        extensions += ["KHR_node_visibility", "KHR_animation_pointer"]
    while len(b.buffer) % 4:
        b.buffer.append(0)
    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "dbkai"},
        "scene": 0,
        "scenes": [scene],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": materials,
        "images": images,
        "textures": textures,
        "samplers": samplers,
        "skins": [skin] if skin is not None else [],
        "animations": animations,
        "buffers": [{"byteLength": len(b.buffer)}] if b.buffer else [],
        "bufferViews": b.views,
        "accessors": b.accessors,
        "extensionsUsed": extensions,
    }
    # glTF forbids an empty array where it allows none at all.
    return _pack_glb({k: v for k, v in gltf.items() if v != []}, bytes(b.buffer))


def _normalized_weights(
    weights: np.ndarray, joints: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each vertex's weights scaled to sum to exactly one in float32, with
    the remainder of the scaling put on its heaviest slot, and the joint of
    a slot without weight set to 0."""
    w = weights.astype(np.float64)
    total = w.sum(axis=1, keepdims=True)
    w = np.divide(w, total, out=w, where=total > 0).astype(np.float32)
    rows = np.arange(len(w))
    w[rows, w.argmax(axis=1)] += np.float32(1) - w.sum(axis=1, dtype=np.float32)
    return w, np.where(w > 0, joints, 0)


def clip_file_name(stem: str, clip: Clip) -> str:
    """``<stem>__<clip>.glb``: the clip's name without its ``.dse`` suffix,
    with anything a file system might dislike replaced."""
    name = re.sub(r"\.dse\d*$", "", clip.name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or f"clip{clip.start}"
    return f"{stem}__{name}.glb"


def export_clips(
    model: Model,
    visible: list[MeshData] | None,
    motions: list[tuple[BoundMotion, Clip]],
    directory: str | Path,
    stem: str,
    palette: int = 0,
) -> list[Path]:
    """Write one ``.glb`` per clip into ``directory``, each holding the model
    and that clip alone, and return the paths written. Every file repeats the
    meshes and textures, which keeps each one self-contained and small next
    to a whole motion set in one file."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for bound, clip in motions:
        path = directory / clip_file_name(stem, clip)
        path.write_bytes(export_glb(model, visible, [(bound, clip)], palette=palette))
        written.append(path)
    return written


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
