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
    assert model.everything() == ({0, 1}, {0})
    assert model.visibility_from_mask(0x00010001) == ({0}, {0})


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


def test_export_clips_writes_one_file_each(model, tmp_path):
    from dbkai.export.gltf import clip_file_name, export_clips

    motion = Motion(dse.parse(build_motion(frames=3)), "spin")
    bound = BoundMotion.bind(model.skeleton, motion)
    clip = motion.clips[0]
    assert clip_file_name("hero", clip) == "hero__000_spin.glb"
    second = type(clip)("weird name/2.dse", 0, 3)
    assert clip_file_name("hero", second) == "hero__weird_name_2.glb"
    written = export_clips(
        model, None, [(bound, clip), (bound, second)], tmp_path, "hero"
    )
    assert [p.name for p in written] == ["hero__000_spin.glb", "hero__weird_name_2.glb"]
    data = written[0].read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    assert [a["name"] for a in doc["animations"]] == ["000_spin"]


def test_alpha_and_culling_follow_the_game(model):
    # The cape's material-select chunk says alpha 0, which the game never
    # reads: the material record's alpha (31) is what it draws with. Its
    # mesh flag 0x10 culls front faces, so the mesh is marked inverted.
    cape = [m for m in model.meshes if m.name.startswith("capeShape")][0]
    assert cape.alpha == 31 and cape.inverted and not cape.double_sided
    body = [m for m in model.meshes if m.name.startswith("bodyShape")][0]
    assert not body.inverted


def test_rest_visibility_falls_back_when_the_preset_hides_all(model):
    # A character preset with no bit for group 1 or part 0 hides the whole
    # fixture, so the model shows everything; a mask that hits shows it.
    assert model.rest_visibility(None) == model.everything()
    assert model.rest_visibility(1 << 5 | 1 << (16 + 9)) == model.everything()
    assert model.rest_visibility(1 << 1 | 1 << 16) == ({1}, {0})


def test_glb_inverted_mesh_is_rewound(model):
    # glTF has no "cull front", so the cape (drawn back-faces-only by the
    # game) is written with its triangles reversed; the body keeps its order.
    glb = export_glb(model, None, [])
    json_len = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20 : 20 + json_len])
    bin_start = 20 + json_len + 8

    def indices(mesh_index: int) -> list[int]:
        prim = doc["meshes"][mesh_index]["primitives"][0]
        acc = doc["accessors"][prim["indices"]]
        view = doc["bufferViews"][acc["bufferView"]]
        at = bin_start + view["byteOffset"] + acc.get("byteOffset", 0)
        return list(struct.unpack_from(f"<{acc['count']}I", glb, at))

    names = [m["name"] for m in doc["meshes"]]
    body = names.index([n for n in names if n.startswith("bodyShape")][0])
    cape = names.index([n for n in names if n.startswith("capeShape")][0])
    assert indices(body)[:3] == [0, 1, 2]
    assert indices(cape)[:3] == [2, 1, 0]


def _glb(data: bytes) -> tuple[dict, bytes]:
    json_len = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20 : 20 + json_len]), data[20 + json_len + 8 :]


def _accessor(doc: dict, binary: bytes, index: int, fmt: str) -> list[tuple]:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    at = view["byteOffset"] + acc.get("byteOffset", 0)
    return list(
        struct.iter_unpack(
            f"<{fmt}", binary[at : at + acc["count"] * struct.calcsize(fmt)]
        )
    )


def test_glb_weights_sum_to_one_and_unused_slots_point_at_joint_0(model):
    # The game's fixed-point weights fall a little short of one, and a slot
    # with no weight may still name a bone; the validator rejects both.
    body = model.meshes[0]
    body.joints = np.array([[1, 1]] * body.vertex_count, dtype=np.uint16)
    body.weights = np.array([[0.9995, 0.0]] * body.vertex_count, dtype=np.float32)
    doc, binary = _glb(export_glb(model, [body], []))
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    assert _accessor(doc, binary, attrs["WEIGHTS_0"], "4f")[0] == (1.0, 0, 0, 0)
    assert _accessor(doc, binary, attrs["JOINTS_0"], "4H")[0] == (1, 0, 0, 0)


def test_glb_gives_several_root_bones_a_common_root(model):
    from dataclasses import replace

    from dbkai.model.skeleton import Skeleton

    sk = model.skeleton
    two = Skeleton(sk.names, sk.hashes, [-1, -1], sk.flags, sk.inverse_bind)
    doc, _ = _glb(export_glb(replace(model, skeleton=two), None, []))
    root = doc["nodes"][2]
    assert root == {"name": "skeleton", "children": [0, 1]}
    assert doc["skins"][0]["skeleton"] == 2
    assert doc["scenes"][0]["nodes"][0] == 2 and 0 not in doc["scenes"][0]["nodes"]
    # One root needs no such node.
    assert "skeleton" not in _glb(export_glb(model, None, []))[0]["skins"][0]


def test_glb_alpha_cutoff_only_with_mask_and_no_empty_arrays(model):
    model.materials[0].alpha = 10
    doc, _ = _glb(export_glb(model, None, []))
    for mat in doc["materials"]:
        assert mat["alphaMode"] == "BLEND" and "alphaCutoff" not in mat
    # Nothing written means no materials, textures or images at all, rather
    # than empty arrays, which glTF forbids.
    doc, _ = _glb(export_glb(model, [], []))
    assert not {"meshes", "materials", "images", "textures", "samplers"} & doc.keys()
    assert "extensionsUsed" not in doc


def test_action_take_follows_the_action(model):
    from dbkai.formats import dsa
    from dbkai.game import GameData
    from dbkai.model.action import action_take
    from dbkai.nds.rom import NdsRom
    from tests.dsa_fixture import build_actions
    from tests.test_game import build_rom

    game = GameData(NdsRom(build_rom()))
    file = dsa.parse(build_actions(), "100000_NORMAL_BALANCE.dsa")
    idle = action_take(model.skeleton, file, file.actions[0], game.motion_set, 0)
    assert idle.name == "100000_NORMAL_BALANCE_1000" and len(idle.poses) == 30
    # Clip 0 (3 frames) from take frame 1, clamped at its end; from frame 10
    # the action names clip 10, which the set lacks, so the pose holds.
    assert [p[1] for p in idle.poses[:4]] == [0, 1, 2, 2]
    assert idle.poses[29] == idle.poses[9]
    assert set(idle.masks) == {0x8023033F}
    # No motion command: the bind pose; the mask starts from the base.
    blink = action_take(model.skeleton, file, file.actions[1], game.motion_set, 0)
    assert blink.poses == [None] * 12
    assert blink.masks[:2] == [0, 0] and blink.masks[2] == 0x804300FF


def test_glb_writes_part_switches(model):
    from dbkai.model.animation import Take

    take = Take("blink", [None] * 4, [0, 0, 0xFFFFFFFF, 0xFFFFFFFF])
    doc, binary = _glb(export_glb(model, None, [], takes=[take]))
    assert {"KHR_node_visibility", "KHR_animation_pointer"} <= set(
        doc["extensionsUsed"]
    )
    anim = doc["animations"][0]
    assert anim["name"] == "blink"
    switches = [c for c in anim["channels"] if c["target"]["path"] == "pointer"]
    assert len(switches) == 2  # one per mesh
    pointer = switches[0]["target"]["extensions"]["KHR_animation_pointer"]["pointer"]
    node = int(pointer.split("/")[2])
    assert pointer.endswith("/extensions/KHR_node_visibility/visible")
    assert doc["nodes"][node]["extensions"]["KHR_node_visibility"] == {"visible": False}
    sampler = anim["samplers"][switches[0]["sampler"]]
    assert sampler["interpolation"] == "STEP"
    times = doc["accessors"][sampler["input"]]
    assert times["count"] == 2 and times["max"] == [pytest.approx(2 / 60)]
    out = doc["accessors"][sampler["output"]]
    view = doc["bufferViews"][out["bufferView"]]
    assert binary[view["byteOffset"] : view["byteOffset"] + 2] == b"\x00\x01"
    # Clips switch nothing and need neither extension.
    assert (
        "KHR_node_visibility"
        not in _glb(export_glb(model, None, []))[0]["extensionsUsed"]
    )
