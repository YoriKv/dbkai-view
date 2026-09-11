"""The session's state machine, on the synthetic fixture and without a
ROM."""

import pytest

from dbkai.formats import dsa, dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui.session import Session
from tests.dsa_fixture import build_actions
from tests.dse_fixture import build_model, build_motion
from tests.test_game import build_rom


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
    session.play(from_start=True)
    assert session.frame == 0 and session.playing


def test_toggle_play_pauses_resumes_and_restarts_a_finished_run(session):
    session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    session.set_clip_frame(1)
    session.toggle_play()
    assert session.playing and session.frame == 1  # resumed, not rewound
    session.toggle_play()
    assert not session.playing and session.frame == 1
    session.loop = False
    session.set_clip_frame(2)
    session.toggle_play()
    assert session.playing and session.frame == 0  # over from the start


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
    session.action_sets = [file]
    session.set_action((file, file.actions[0]))
    # 0x8023033F: groups 0-5, 8, 9 and parts 0, 1, 5, 15 - of which the
    # fixture has groups 0 and 1 and part 0.
    assert session.visibility.groups == {0, 1} and session.visibility.parts == {0}
    session.set_action((file, file.actions[1]))
    assert session.action_mask() is None  # frame 0: no command yet
    session.set_action_frame(2)
    assert session.action_mask() == 0x804300FF
    assert session.visibility.groups == {0, 1}
    session.set_action(None)
    assert session.action is None


def test_apply_mask_and_rest(session):
    session.apply_mask(0x00010001)  # group 0, part 0 only
    assert session.visibility.groups == {0} and session.visibility.parts == {0}
    session.reset_visibility()
    assert session.visibility.groups == {0, 1}


def test_action_playback_drives_clip_and_frame(qapp):
    from dbkai.game import GameData
    from dbkai.nds.rom import NdsRom

    s = Session()
    s.game = GameData(NdsRom(build_rom()))
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    assert s.action_sets and s.action_sets[0].name == "100000_NORMAL_BALANCE.dsa"
    file = s.action_sets[0]
    s.set_action((file, file.actions[0]))
    # The idle action's first segment plays clip number 0 ("000_spin") from
    # take frame 1, which is the clip's first frame.
    assert s.clip is not None and s.clip.number == 0
    assert s.frame == 0 and "clip 00000" in s.action_clip_name()
    s.set_action_frame(2)
    assert s.frame == s.clip.frame_for_take(3)
    s.set_action_frame(10)
    assert s.clip.number == 0  # the fixture set has no clip 10: stays put
    assert s.visibility.parts == {0}
    s.play()
    s._tick()
    assert s.action_frame == 11
    s.set_action(None)
    assert not s.playing


def test_action_file_asset_joins_the_action_list(qapp):
    from dbkai.game import GameData
    from dbkai.nds.rom import NdsRom

    s = Session()
    s.game = GameData(NdsRom(build_rom()))
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    before = [f.name for f in s.action_sets]
    s.load_asset(s.game.find("/debug/110000_TALL_POWER.dsa"))
    assert [f.name for f in s.action_sets] == before + ["110000_TALL_POWER.dsa"]
    s.load_asset(s.game.find("/debug/110000_TALL_POWER.dsa"))  # again: replaces
    assert [f.name for f in s.action_sets] == before + ["110000_TALL_POWER.dsa"]


def test_added_action_file_can_be_removed_but_not_the_models_own(qapp):
    from dbkai.game import GameData
    from dbkai.nds.rom import NdsRom

    s = Session()
    s.game = GameData(NdsRom(build_rom()))
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    own = [f.name for f in s.action_sets]
    s.load_asset(s.game.find("/debug/110000_TALL_POWER.dsa"))
    added = s.action_sets[-1]
    assert s.is_added_action_set(added.name) and not s.is_added_action_set(own[0])
    s.set_action((added, added.actions[0]))
    s.remove_action_set(added.name)
    assert [f.name for f in s.action_sets] == own and s.action is None
    s.remove_action_set(own[0])
    assert [f.name for f in s.action_sets] == own


def test_action_colour_scheme_does_not_outlive_the_action(session):
    file = dsa.parse(build_actions(), "fixture.dsa")
    session.action_sets = [file]
    session.set_action((file, file.actions[0]))
    session.set_action_frame(10)  # the colour command picks scheme 2 here
    assert session.options.palette == 2
    session.set_action(None)
    assert session.options.palette == 0
    session.set_option("palette", 1)
    session.set_action((file, file.actions[0]))
    session.set_action_frame(10)
    assert session.options.palette == 2
    session._set_model(session.model, None, None)  # another model: back to default
    assert session.options.palette == 0 and session.action is None


def test_an_action_set_can_be_added_before_any_model(qapp):
    s = Session()
    file = dsa.parse(build_actions(), "fixture.dsa")
    s.add_action_set(file)
    assert s.is_added_action_set(file.name)
    s.remove_action_set(file.name)
    assert s.action_sets == []


@pytest.mark.parametrize(
    "leave",
    [
        lambda s: s.set_motion(Motion(dse.parse(build_motion(frames=3)), "other")),
        lambda s: s.set_clip(s.clip),
        lambda s: s.set_clip_frame(1),
    ],
    ids=["another motion", "a clip", "scrubbing"],
)
def test_choosing_the_pose_another_way_leaves_the_action(session, leave):
    session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    file = dsa.parse(build_actions(), "fixture.dsa")
    session.action_sets = [file]
    session.set_option("palette", 1)
    session.set_action((file, file.actions[0]))
    session.set_action_frame(10)  # the colour command picks scheme 2 here
    assert session.options.palette == 2
    seen = []
    session.actions_changed.connect(lambda: seen.append(1))
    leave(session)
    assert session.action is None and session.action_frame == 0
    assert session.options.palette == 1  # the scheme the action chose is undone
    assert seen  # the Actions tab hears of it


def test_a_new_rom_takes_the_old_roms_model_with_it(tmp_path, qapp):
    rom = tmp_path / "game.nds"
    rom.write_bytes(build_rom())
    s = Session()
    s.open_rom(rom)
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    assert s.model is not None and s.action_sets
    s.open_rom(rom)
    assert s.model is None and s.asset is None
    assert s.motion is None and s.action_sets == [] and s.visible_meshes() == []


def test_a_new_rom_keeps_a_model_opened_from_a_file(tmp_path, qapp):
    rom = tmp_path / "game.nds"
    rom.write_bytes(build_rom())
    model = tmp_path / "model.dse"
    model.write_bytes(build_model())
    s = Session()
    s.open_file(model)
    s.open_rom(rom)
    assert s.model is not None and s.model_path == model


def test_a_preset_is_chosen_like_an_action(qapp):
    from dbkai.game import GameData
    from dbkai.nds.rom import NdsRom

    s = Session()
    s.game = GameData(NdsRom(build_rom()))
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    file = s.action_sets[0]
    s.set_action((file, file.actions[0]))
    s.set_action_frame(10)  # the colour command picks scheme 2 here
    s.play()
    seen = []
    s.actions_changed.connect(lambda: seen.append(1))
    s.set_preset(10000)  # 0x802300FF: groups 0-7, parts 0, 1, 5, 15
    assert s.action is None and not s.playing and s.options.palette == 0
    assert s.preset == 10000 and seen
    assert s.visibility.groups == {0, 1} and s.visibility.parts == {0}
    # An action takes its place, and it takes an action's.
    s.set_action((file, file.actions[0]))
    assert s.preset is None
    s.set_preset(10000)
    assert s.action is None and s.preset == 10000
    # Setting the parts by hand leaves it; the parts stay as set.
    seen.clear()
    s.set_group(1, False)
    assert s.preset is None and seen and s.visibility.groups == {0}
    s.set_preset(10000)
    s.set_preset(None)
    assert s.preset is None and s.visibility.groups == {0, 1}
