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
from dbkai.formats.texture import TextureFormat
from dbkai.model.animation import BoundMotion, Clip, Take
from dbkai.model.scene import MaterialData, MeshData, Model
from dbkai.model.skeleton import LocalPose

#: The game runs at 60 frames per second; motions are keyed every frame.
FRAME_RATE = 60.0

_COMPONENT = {
    np.dtype(np.float32): 5126,
    np.dtype(np.uint16): 5123,
    np.dtype(np.uint32): 5125,
    np.dtype(np.uint8): 5121,
}
_TYPE = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_NEAREST = 9728
_CLAMP, _MIRRORED_REPEAT, _REPEAT = 33071, 33648, 10497
_TRIANGLES = 4

#: A bone scale within this of one is fixed-point noise and not written.
#: The bind matrices of unscaled bones stay under 1e-3; scaled props start
#: at 1e-2.
_SCALE_TOLERANCE = 5e-3


@dataclass
class _Builder:
    """The binary chunk, with the buffer views and accessors over it."""

    buffer: bytearray = field(default_factory=bytearray)
    views: list[dict[str, Any]] = field(default_factory=list)
    accessors: list[dict[str, Any]] = field(default_factory=list)

    def view(self, data: bytes, target: int | None = None) -> int:
        self._align()
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": len(self.buffer),
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        self.buffer.extend(data)
        self.views.append(view)
        return len(self.views) - 1

    def accessor(
        self, array: np.ndarray, target: int | None = None, minmax: bool = False
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
        if minmax:
            flat = array.reshape(count, width)
            acc["min"] = [float(v) for v in flat.min(axis=0)]
            acc["max"] = [float(v) for v in flat.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def finish(self) -> bytes:
        self._align()
        return bytes(self.buffer)

    def _align(self) -> None:
        while len(self.buffer) % 4:
            self.buffer.append(0)


def export_glb(
    model: Model,
    visible: list[MeshData] | None = None,
    motions: list[tuple[BoundMotion, Clip]] | None = None,
    palette: int = 0,
    takes: list[Take] | None = None,
) -> bytes:
    """Build the ``.glb`` bytes.

    ``visible`` restricts the meshes written (default: all of them); each is
    its own node, so an importer can toggle parts. ``motions`` are ``(bound
    motion, clip)`` pairs to write as animations; the clip's frames become
    keyframes at :data:`FRAME_RATE`. ``takes`` are further animations, such
    as actions, written after the clips; a take's masks key the visibility
    of every mesh written.
    """
    w = _Writer(model, palette)
    w.add_skeleton()
    for mesh in visible if visible is not None else model.meshes:
        w.add_mesh(mesh)
    for bound, clip in motions or []:
        w.add_take(Take.of_clip(bound, clip))
    for take in takes or []:
        w.add_take(take)
    return w.glb()


class _Writer:
    """One ``.glb`` as it is built. Textures, samplers, images and materials
    are written on first use, so the file carries only what its meshes draw
    with."""

    def __init__(self, model: Model, palette: int) -> None:
        self.model = model
        self.palette = palette
        self.b = _Builder()
        self.rest = model.skeleton.bind_pose()
        self.nodes: list[dict[str, Any]] = []
        self.roots: list[int] = []
        self.skin: dict[str, Any] | None = None
        self.meshes: list[dict[str, Any]] = []
        #: Every mesh written, with its node.
        self.written: list[tuple[MeshData, int]] = []
        self.materials: list[dict[str, Any]] = []
        self.textures: list[dict[str, Any]] = []
        self.samplers: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self.animations: list[dict[str, Any]] = []
        self.switches = False
        self._image_of: dict[int, int | None] = {}
        self._material_of: dict[tuple[int, bool], int] = {}
        self._shown: dict[int, tuple[set[int], set[int]]] = {}

    # -- skeleton -------------------------------------------------------------

    def add_skeleton(self) -> None:
        """One node per bone, in bone order so node *i* is bone *i*, posed
        at bind; then the skin, when there are bones."""
        sk = self.model.skeleton
        for i, pose in enumerate(self.rest):
            node: dict[str, Any] = {"name": sk.names[i]}
            if any(abs(v) > 1e-9 for v in pose.translation):
                node["translation"] = [float(v) for v in pose.translation]
            if abs(pose.rotation[3]) < 1 - 1e-9:
                node["rotation"] = [float(v) for v in pose.rotation]
            if _scaled(pose):
                node["scale"] = [float(v) for v in pose.scale]
            children = sk.children(i)
            if children:
                node["children"] = children
            self.nodes.append(node)
        n = len(sk)
        if not n:
            return
        self.roots = [i for i in range(n) if sk.parents[i] < 0]
        # glTF's matrices are column-major: the transpose, row by row.
        ibm = sk.inverse_bind.transpose(0, 2, 1).astype(np.float32)
        self.skin = {
            "joints": list(range(n)),
            "inverseBindMatrices": self.b.accessor(ibm.reshape(n, 16)),
        }
        if len(self.roots) > 1:
            self.nodes.append({"name": "skeleton", "children": self.roots})
            self.roots = [len(self.nodes) - 1]
            self.skin["skeleton"] = self.roots[0]

    # -- meshes ---------------------------------------------------------------

    def add_mesh(self, mesh: MeshData) -> None:
        if mesh.vertex_count == 0 or mesh.face_count == 0:
            return
        b = self.b
        attrs = {
            "POSITION": b.accessor(
                mesh.positions.astype(np.float32), _ARRAY_BUFFER, minmax=True
            ),
            "TEXCOORD_0": b.accessor(mesh.uvs.astype(np.float32), _ARRAY_BUFFER),
            "COLOR_0": b.accessor(mesh.colors.astype(np.float32), _ARRAY_BUFFER),
        }
        if self.skin is not None:
            weights, joints = _normalized_weights(mesh.weights, mesh.joints)
            k = joints.shape[1]
            # Four influences per JOINTS_n / WEIGHTS_n pair, the last padded.
            for n, first in enumerate(range(0, k, 4)):
                cols = min(4, k - first)
                j = np.zeros((mesh.vertex_count, 4), dtype=np.uint16)
                w = np.zeros((mesh.vertex_count, 4), dtype=np.float32)
                j[:, :cols] = joints[:, first : first + cols]
                w[:, :cols] = weights[:, first : first + cols]
                attrs[f"JOINTS_{n}"] = b.accessor(j, _ARRAY_BUFFER)
                attrs[f"WEIGHTS_{n}"] = b.accessor(w, _ARRAY_BUFFER)
        # A mesh the game draws back-faces-only is wound the other way round.
        indices = mesh.indices[:, ::-1] if mesh.inverted else mesh.indices
        prim: dict[str, Any] = {
            "attributes": attrs,
            "indices": b.accessor(
                indices.astype(np.uint32).reshape(-1), _ELEMENT_ARRAY_BUFFER
            ),
            "mode": _TRIANGLES,
        }
        material = self._material(mesh)
        if material is not None:
            prim["material"] = material
        self.meshes.append({"name": mesh.name, "primitives": [prim]})
        node: dict[str, Any] = {
            "name": mesh.name,
            "mesh": len(self.meshes) - 1,
            "extras": {"group": mesh.group, "part": mesh.part},
        }
        if self.skin is not None:
            node["skin"] = 0
        self.nodes.append(node)
        self.roots.append(len(self.nodes) - 1)
        self.written.append((mesh, len(self.nodes) - 1))

    # -- materials ------------------------------------------------------------

    def _material(self, mesh: MeshData) -> int | None:
        """The mesh's material; a double-sided mesh gets its own instance."""
        model = self.model
        if mesh.material >= len(model.materials):
            return None
        key = (mesh.material, mesh.double_sided)
        if key in self._material_of:
            return self._material_of[key]
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
        tex = self._texture(m)
        if tex is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex}
            src = model.textures[m.texture]
            if src.color0_transparent:
                mat["alphaMode"] = "MASK"
            elif src.format in (TextureFormat.A3I5, TextureFormat.A5I3):
                mat["alphaMode"] = "BLEND"
        if m.alpha < 31:
            mat["alphaMode"] = "BLEND"
        if mat.get("alphaMode") == "MASK":
            mat["alphaCutoff"] = 0.5
        self.materials.append(mat)
        self._material_of[key] = len(self.materials) - 1
        return self._material_of[key]

    def _texture(self, m: MaterialData) -> int | None:
        source = self._image(m.texture) if m.texture is not None else None
        if source is None:
            return None
        entry = {"sampler": self._sampler(m), "source": source}
        return _index_of(self.textures, entry)

    def _sampler(self, m: MaterialData) -> int:
        def wrap(repeat: bool, flip: bool) -> int:
            return _MIRRORED_REPEAT if flip else _REPEAT if repeat else _CLAMP

        entry = {
            "magFilter": _NEAREST,
            "minFilter": _NEAREST,
            "wrapS": wrap(m.repeat_s, m.flip_s),
            "wrapT": wrap(m.repeat_t, m.flip_t),
        }
        return _index_of(self.samplers, entry)

    def _image(self, index: int) -> int | None:
        """The texture as an embedded PNG in the palette asked for (its last
        one when it has fewer), or ``None`` when the file ships no pixels."""
        if index not in self._image_of:
            t = self.model.textures[index]
            if not t.available:
                self._image_of[index] = None
            else:
                rgba = t.rgba(min(self.palette, t.palette_count - 1))
                png = encode_png(rgba.width, rgba.height, rgba.pixels)
                self.images.append(
                    {
                        "bufferView": self.b.view(png),
                        "mimeType": "image/png",
                        "name": t.name,
                    }
                )
                self._image_of[index] = len(self.images) - 1
        return self._image_of[index]

    # -- animations -----------------------------------------------------------

    def add_take(self, take: Take) -> None:
        """``take`` as one animation: every bone keyed every frame, and the
        switches of every mesh written when the take has masks. A take that
        would key nothing is left out, since glTF forbids an empty one."""
        count = len(take.poses)
        if count <= 0:
            return
        frame_times = np.arange(count, dtype=np.float32) / FRAME_RATE
        channels: list[dict[str, Any]] = []
        samplers: list[dict[str, Any]] = []

        def key(
            times: int, values: np.ndarray, target: dict[str, Any], step: bool = False
        ) -> None:
            samplers.append(
                {
                    "input": times,
                    "output": self.b.accessor(values),
                    "interpolation": "STEP" if step else "LINEAR",
                }
            )
            channels.append({"sampler": len(samplers) - 1, "target": target})

        if self.rest:
            every_frame = self.b.accessor(frame_times, minmax=True)
            rot, trans, scale = self._sample(take)
            for i, rest in enumerate(self.rest):
                key(every_frame, rot[:, i], {"node": i, "path": "rotation"})
                key(every_frame, trans[:, i], {"node": i, "path": "translation"})
                # A scaled bone the motion drives loses its scale, as in the
                # viewer; the node's own scale holds otherwise.
                if _scaled(rest) and np.any(
                    scale[:, i] != np.asarray(rest.scale, dtype=np.float32)
                ):
                    key(every_frame, scale[:, i], {"node": i, "path": "scale"})
        if take.masks is not None:
            for mesh, node in self.written:
                visible = self._visibility(mesh, take.masks, count)
                keys = [0] + [
                    k for k in range(1, count) if visible[k] != visible[k - 1]
                ]
                # The node's own value is what the first take shows at its start.
                self.nodes[node].setdefault("extensions", {}).setdefault(
                    "KHR_node_visibility", {"visible": bool(visible[0])}
                )
                pointer = f"/nodes/{node}/extensions/KHR_node_visibility/visible"
                key(
                    self.b.accessor(frame_times[keys], minmax=True),
                    visible[keys],
                    {
                        "path": "pointer",
                        "extensions": {"KHR_animation_pointer": {"pointer": pointer}},
                    },
                    step=True,
                )
                self.switches = True
        if channels:
            self.animations.append(
                {"name": take.name, "channels": channels, "samplers": samplers}
            )

    def _sample(self, take: Take) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Every bone's rotation, translation and scale in every frame of
        ``take``, as ``(frames, bones, 4 | 3 | 3)`` float32 arrays."""
        frames = [
            self.rest if posed is None else posed[0].local_poses(posed[1], self.rest)
            for posed in take.poses
        ]
        shape = (len(frames), len(self.rest))
        q = np.array(
            [[p.rotation for p in f] for f in frames], dtype=np.float64
        ).reshape(*shape, 4)
        norm = np.linalg.norm(q, axis=-1, keepdims=True)
        q = np.where(norm > 0, q / np.where(norm > 0, norm, 1), (0, 0, 0, 1))
        rot = q.astype(np.float32)
        # Keep quaternion continuity so importers interpolate the short way:
        # a key whose dot with the one before is negative flips, and every
        # flip carries on to the keys after it.
        dots = np.einsum("fbi,fbi->fb", rot[1:], rot[:-1])
        rot[1:] *= np.cumprod(
            np.where(dots < 0, np.float32(-1), np.float32(1)), axis=0
        )[..., None]
        trans = np.array(
            [[p.translation for p in f] for f in frames], dtype=np.float32
        ).reshape(*shape, 3)
        scale = np.array(
            [[p.scale for p in f] for f in frames], dtype=np.float32
        ).reshape(*shape, 3)
        return rot, trans, scale

    def _visibility(self, mesh: MeshData, masks: list[int], count: int) -> np.ndarray:
        """Per frame, 1 when ``mesh`` shows under the frame's mask."""
        visible = np.zeros(count, dtype=np.uint8)
        for k, mask in enumerate(masks[:count]):
            if mask not in self._shown:
                self._shown[mask] = self.model.visibility_from_mask(mask)
            groups, parts = self._shown[mask]
            visible[k] = mesh.group in groups and mesh.part in parts
        return visible

    # -- the file -------------------------------------------------------------

    def glb(self) -> bytes:
        scene: dict[str, Any] = {"name": self.model.name}
        if self.roots:
            scene["nodes"] = self.roots
        extensions = ["KHR_materials_unlit"] if self.materials else []
        if self.switches:
            extensions += ["KHR_node_visibility", "KHR_animation_pointer"]
        binary = self.b.finish()
        gltf: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": "dbkai"},
            "scene": 0,
            "scenes": [scene],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "images": self.images,
            "textures": self.textures,
            "samplers": self.samplers,
            "skins": [self.skin] if self.skin is not None else [],
            "animations": self.animations,
            "buffers": [{"byteLength": len(binary)}] if binary else [],
            "bufferViews": self.b.views,
            "accessors": self.b.accessors,
            "extensionsUsed": extensions,
        }
        # glTF forbids an empty array where it allows none at all.
        return _pack_glb({k: v for k, v in gltf.items() if v != []}, binary)


def _scaled(pose: LocalPose) -> bool:
    return any(abs(v - 1) > _SCALE_TOLERANCE for v in pose.scale)


def _index_of(entries: list[dict[str, Any]], entry: dict[str, Any]) -> int:
    """``entry``'s index in ``entries``, appending it when it is new."""
    if entry not in entries:
        entries.append(entry)
    return entries.index(entry)


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
    """The GLB container: header, the JSON chunk padded with spaces, then the
    binary chunk (already 4-byte aligned), left out when there is none."""
    text = json.dumps(gltf, separators=(",", ":")).encode()
    while len(text) % 4:
        text += b" "
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
