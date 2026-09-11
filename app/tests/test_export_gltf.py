"""The glTF writer's handling of scaled bones, quaternion continuity and
takes that key nothing, on the synthetic fixture."""

import json
import struct
from dataclasses import replace

import numpy as np
import pytest

from dbkai.export.gltf import export_glb
from dbkai.formats import dse
from dbkai.model import scene
from dbkai.model.animation import BoundMotion, Motion, Take
from dbkai.model.skeleton import LocalPose, Skeleton
from tests.dse_fixture import build_model, build_motion


@pytest.fixture
def model():
    return scene.build(dse.parse(build_model()))


def _glb(data: bytes) -> tuple[dict, bytes]:
    json_len = struct.unpack_from("<I", data, 12)[0]
    rest = data[20 + json_len :]
    return json.loads(data[20 : 20 + json_len]), rest[8:]


def _floats(doc: dict, binary: bytes, index: int) -> np.ndarray:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc["type"]]
    at = view["byteOffset"]
    return np.frombuffer(
        binary[at : at + 4 * width * acc["count"]], dtype="<f4"
    ).reshape(acc["count"], width)


def _scaled_tip(model, factor: float = 10.0):
    """The fixture with its tip bone bound at ``factor`` times its size, as
    a few props are."""
    sk = model.skeleton
    inverse = sk.inverse_bind.copy()
    inverse[1] = np.linalg.inv(sk.bind_world[1] @ np.diag([factor] * 3 + [1]))
    scaled = Skeleton(sk.names, sk.hashes, sk.parents, sk.flags, inverse)
    return replace(model, skeleton=scaled)


def test_a_scaled_bind_writes_the_node_scale(model):
    # The node's transform and the inverse bind must agree at rest, or the
    # mesh on the bone is drawn a tenth of its size.
    doc, _ = _glb(export_glb(_scaled_tip(model), None, []))
    assert doc["nodes"][1]["scale"] == pytest.approx([10, 10, 10])
    assert "scale" not in doc["nodes"][0]
    # Unscaled bones write none, as before.
    assert all("scale" not in n for n in _glb(export_glb(model, None, []))[0]["nodes"])


def test_a_scaled_bone_the_motion_drives_keys_its_scale(model):
    scaled = _scaled_tip(model)
    motion = Motion(dse.parse(build_motion(frames=3)), "spin")
    bound = BoundMotion.bind(scaled.skeleton, motion)
    # Frame 0 is the bind pose (the node's scale), then the motion drives
    # the tip, which has no scale of its own.
    take = Take("mixed", [None, (bound, 1), (bound, 2)])
    doc, binary = _glb(export_glb(scaled, None, [], takes=[take]))
    channels = doc["animations"][0]["channels"]
    scales = [c for c in channels if c["target"]["path"] == "scale"]
    assert [c["target"]["node"] for c in scales] == [1]
    sampler = doc["animations"][0]["samplers"][scales[0]["sampler"]]
    values = _floats(doc, binary, sampler["output"])
    assert values.tolist() == [[10, 10, 10], [1, 1, 1], [1, 1, 1]]
    # A clip on an unscaled model keys no scale.
    doc, _ = _glb(export_glb(model, None, [(bound, motion.clips[0])]))
    assert all(c["target"]["path"] != "scale" for c in doc["animations"][0]["channels"])


def test_rotations_keep_continuity_across_a_flip(model):
    # q and -q are the same rotation; the writer flips a key whose dot with
    # the one before is negative, and the flip carries on to later keys.
    q = (0.0, 0.0, 0.6, 0.8)
    neg = tuple(-v for v in q)
    rest = model.skeleton.bind_pose()

    class Posed:
        def __init__(self, rotation):
            self.rotation = rotation

        def local_poses(self, _frame, _rest):
            return [rest[0], LocalPose(self.rotation, rest[1].translation)]

    frames = [q, neg, neg, q, (0.0, 0.0, 1.2, 1.6)]  # the last not unit
    take = Take("flip", [(Posed(r), 0) for r in frames])  # type: ignore[misc]
    doc, binary = _glb(export_glb(model, None, [], takes=[take]))
    anim = doc["animations"][0]
    rot = [
        c for c in anim["channels"] if c["target"] == {"node": 1, "path": "rotation"}
    ][0]
    values = _floats(doc, binary, anim["samplers"][rot["sampler"]]["output"])
    assert values == pytest.approx(np.array([q] * 5), abs=1e-6)


def test_a_take_that_keys_nothing_is_left_out(model):
    # With no bones and no masks there is nothing to key, and glTF forbids
    # an animation without channels.
    boneless = replace(model, skeleton=Skeleton([], [], [], [], np.zeros((0, 4, 4))))
    doc, _ = _glb(export_glb(boneless, None, [], takes=[Take("still", [None] * 3)]))
    assert "animations" not in doc and "skins" not in doc
    assert doc["meshes"]


def test_an_empty_file_has_no_binary_chunk(model):
    boneless = replace(model, skeleton=Skeleton([], [], [], [], np.zeros((0, 4, 4))))
    data = export_glb(boneless, [], [])
    json_len = struct.unpack_from("<I", data, 12)[0]
    # Header and JSON chunk only: no zero-length binary chunk after them.
    assert len(data) == 12 + 8 + json_len
    assert struct.unpack_from("<I", data, 8)[0] == len(data)
    assert "buffers" not in _glb(data)[0]
