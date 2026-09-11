"""Skeleton, animation binding, scene building and the glTF writer, on the
synthetic fixture."""

import json
import struct

import numpy as np
import pytest

from dbkai.export.gltf import export_glb
from dbkai.export.png import encode_png
from dbkai.formats import dse
from dbkai.model import math3d, scene
from dbkai.model.animation import BoundMotion, Motion
from dbkai.model.skeleton import LocalPose
from tests.dse_fixture import build_model, build_motion


@pytest.fixture
def model():
    return scene.build(dse.parse(build_model()))


def test_ds_matrix_roundtrip():
    vals = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 3.0, 4.0, 5.0]
    m = math3d.from_ds(vals)
    assert math3d.to_ds(m) == pytest.approx(vals)
    # Row-vector convention: x axis maps to +y.
    assert math3d.transform_points(m, np.array([[1.0, 0, 0]]))[0] == pytest.approx(
        [3.0, 5.0, 5.0]
    )


def test_quaternion_roundtrip():
    q = (0.1, 0.7, -0.2, 0.67)
    m = math3d.quat_to_matrix(q)
    n = np.linalg.norm(q)
    assert math3d.matrix_to_quat(m) == pytest.approx(tuple(v / n for v in q), abs=1e-9)


def test_skeleton_bind_and_fk(model):
    sk = model.skeleton
    assert sk.order == [0, 1]
    assert sk.bind_world[1][:3, 3] == pytest.approx([0, 2, 0])
    assert sk.bind_local[1][:3, 3] == pytest.approx([0, 2, 0])
    rest = sk.bind_pose()
    assert rest[1].translation == pytest.approx((0, 2, 0))
    world = sk.world_matrices([p.matrix() for p in rest])
    assert np.allclose(world, sk.bind_world)
    assert np.allclose(sk.skin_matrices(world), np.eye(4))


def test_scene_meshes_are_model_space(model):
    body, cape = model.meshes
    # The tip bone sits at y=2: bone-local vertices move up by 2.
    assert body.positions[:, 1].min() == pytest.approx(2.0)
    assert body.face_count == 1 + 2 and body.vertex_count == 7
    assert body.uvs.max() == pytest.approx(1.0)
    assert tuple(body.colors[3]) == pytest.approx((1, 0, 0))
    assert body.joints[0, 0] == 1 and body.weights[0, 0] == 1.0
    # Skinned mesh keeps model space, and its shift doubles the stored values.
    assert cape.skinned and cape.shift == 1
    assert cape.positions[2] == pytest.approx([0, 6, 0])
    assert cape.weights[1].tolist() == [0.5, 0.5]
    assert model.groups == [0, 1] and model.parts == [0]
    assert model.default_visibility() == ({0, 1}, {0})


def test_skinning_moves_with_bones(model):
    sk = model.skeleton
    rest = sk.bind_pose()
    moved = [rest[0], LocalPose(rest[1].rotation, (1.0, 2.0, 0.0))]
    world = sk.world_matrices([p.matrix() for p in moved])
    body = scene.skin(model.meshes[0], sk.skin_matrices(world))
    assert body[0] == pytest.approx(model.meshes[0].positions[0] + [1, 0, 0])
    cape = scene.skin(model.meshes[1], sk.skin_matrices(world))
    # Half root (still), half tip (moved by +1 in x).
    assert cape[1] == pytest.approx(model.meshes[1].positions[1] + [0.5, 0, 0])


def test_motion_binds_by_name(model):
    motion = Motion(dse.parse(build_motion(frames=3)), "spin")
    bound = BoundMotion.bind(model.skeleton, motion)
    assert bound.mapping == [0, 1] and bound.matched == 2
    clip = motion.clips[0]
    assert clip.name == "000_spin" and clip.frame_count == 3
    world = bound.world_matrices(2)
    # The tip rotated 90 degrees about z: its x axis now points along +y.
    assert world[1][:3, 0] == pytest.approx([0, 1, 0], abs=1e-3)


def test_glb_structure(model):
    motion = Motion(dse.parse(build_motion(frames=3)), "spin")
    bound = BoundMotion.bind(model.skeleton, motion)
    data = export_glb(model, None, [(bound, motion.clips[0])])
    assert data[:4] == b"glTF"
    length = struct.unpack_from("<I", data, 8)[0]
    assert length == len(data)
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    assert doc["asset"]["version"] == "2.0"
    assert [n["name"] for n in doc["nodes"][:2]] == ["root", "tip"]
    assert doc["nodes"][0]["children"] == [1]
    assert doc["skins"][0]["joints"] == [0, 1]
    assert len(doc["meshes"]) == 2
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    assert {"POSITION", "TEXCOORD_0", "COLOR_0", "JOINTS_0", "WEIGHTS_0"} <= set(attrs)
    assert doc["images"][0]["mimeType"] == "image/png"
    assert doc["samplers"][0]["wrapT"] == 33648  # flip T -> mirrored repeat
    anim = doc["animations"][0]
    assert anim["name"] == "000_spin" and len(anim["channels"]) == 4
    assert doc["accessors"][anim["samplers"][0]["input"]]["count"] == 3
    # Every accessor stays inside the binary chunk.
    bin_len = struct.unpack_from("<I", data, 20 + json_len)[0]
    for view in doc["bufferViews"]:
        assert view["byteOffset"] + view["byteLength"] <= bin_len


def test_png_encoder_roundtrips_header():
    png = encode_png(2, 1, bytes([255, 0, 0, 255, 0, 255, 0, 128]))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack_from(">II", png, 16) == (2, 1)
    with pytest.raises(ValueError):
        encode_png(2, 2, b"")
