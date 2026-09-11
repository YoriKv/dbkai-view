"""The session's state machine, on the synthetic fixture and without a
ROM."""

import pytest

from dbkai.formats import dsa, dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui.session import Session
from tests.dsa_fixture import build_actions
from tests.dse_fixture import build_model, build_motion


@pytest.fixture
def session(qapp):
    s = Session()
    s._set_model(scene.build(dse.parse(build_model()), "fixture"), None, None)
    return s


def test_model_load_sets_defaults(session):
    assert session.model is not None
    assert session.visibility.groups == {0, 1}
    assert session.visibility.parts == {0}
    assert len(session.visible_meshes()) == 2
    assert session.world_matrices().shape == (2, 4, 4)


def test_visibility_toggles(session):
    seen = []
    session.visibility_changed.connect(lambda: seen.append(1))
    session.set_group(1, False)
    assert [m.name for m in session.visible_meshes()] == ["bodyShape"]
    session.set_mesh_hidden(0, True)
    assert session.visible_meshes() == []
    session.reset_visibility()
    assert len(session.visible_meshes()) == 2
    session.show_everything()
    assert session.visibility.hidden == set()
    assert len(seen) == 4


def test_motion_clip_and_frames(session):
    session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    assert session.bound is not None and session.bound.matched == 2
    assert session.clip is not None and session.clip.frame_count == 3
    frames = []
    session.frame_changed.connect(frames.append)
    session.set_clip_frame(2)
    assert session.frame == 2 and session.clip_frame == 2
    session.set_clip_frame(99)  # clamped to the clip
    assert session.frame == 2
    session.set_clip_frame(0)
    assert frames == [2, 0]
    # Posed matrices differ from the bind pose once the tip has rotated.
    session.set_clip_frame(2)
    world = session.world_matrices()
    assert abs(world[1][0, 0]) < 0.01
    session.set_motion(None)
    assert session.clip is None and session.frame == 0


def test_playback_steps_and_loops(session):
    session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    session.play()
    assert session.playing
    session._tick()
    assert session.frame == 1
    session._tick()
    session._tick()
    assert session.frame == 0  # looped
    session.loop = False
    session._tick()
    session._tick()
    session._tick()
    assert session.frame == 2 and not session.playing


def test_options_emit_only_on_change(session):
    seen = []
    session.options_changed.connect(lambda: seen.append(1))
    session.set_option("wireframe", True)
    session.set_option("wireframe", True)
    assert session.options.wireframe and len(seen) == 1


def test_open_file_accepts_models_and_motions(tmp_path, qapp):
    s = Session()
    model_path = tmp_path / "m.dse"
    model_path.write_bytes(build_model())
    s.open_file(model_path)
    assert s.model is not None and s.model.name == "m.dse"
    motion_path = tmp_path / "sm_x.dse"
    motion_path.write_bytes(build_motion())
    s.open_file(motion_path)
    assert s.motion is not None and s.bound is not None
    (tmp_path / "bad.dse").write_bytes(b"nope")
    with pytest.raises(ValueError):
        s.open_file(tmp_path / "bad.dse")


def test_action_drives_visibility(session):
    session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    file = dsa.parse(build_actions(), "fixture.dsa")
    session.action_files = [file]
    session.set_action((file, file.actions[0]))
    # 0x8023033F: groups 0-5, 8, 9 and parts 0, 1, 5, 15 - of which the
    # fixture has groups 0 and 1 and part 0.
    assert session.visibility.groups == {0, 1} and session.visibility.parts == {0}
    session.set_action((file, file.actions[1]))
    assert session.action_mask() is None  # frame 0: no command yet
    session.set_clip_frame(2)
    assert session.action_mask() == 0x804300FF
    assert session.visibility.groups == {0, 1}
    session.set_action(None)
    assert session.action is None


def test_apply_mask_and_rest(session):
    session.apply_mask(0x00010001)  # group 0, part 0 only
    assert session.visibility.groups == {0} and session.visibility.parts == {0}
    session.reset_visibility()
    assert session.visibility.groups == {0, 1}
