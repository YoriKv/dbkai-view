"""The action file parser and the masks it yields, on the synthetic file."""

from dbkai.formats import dsa
from tests.dsa_fixture import build_actions


def test_parse_actions_and_commands():
    f = dsa.parse(build_actions(), "fixture.dsa")
    assert [a.action_id for a in f.actions] == [1000, 2000]
    assert [a.duration for a in f.actions] == [30, 12]
    assert f.motion_sets == [100000]
    assert [r.number for r in f.resources] == [0, 10]
    assert not f.resources[0].embedded
    idle, hit = f.actions
    kinds = [type(c).__name__ for c in idle.commands]
    assert kinds == [
        "VisibilityCommand",
        "ColorCommand",
        "LinkCommand",
        "MotionCommand",
    ]
    assert idle.commands[2].target == 1
    assert idle.mask_at(0) == 0x8023033F and idle.mask_at(29) == 0x8023033F
    assert idle.scheme_at(0) is None and idle.scheme_at(10) == 2
    assert len(hit.commands) == 1
    tracked = hit.commands[0]
    assert isinstance(tracked, dsa.VisibilityCommand)
    assert tracked.groups_track is not None
    assert hit.mask_at(0) is None  # the command starts at frame 2
    assert hit.mask_at(2) == 0x804300FF
    assert hit.mask_at(6) == 0x8023033F
    assert hit.mask_at(9) == 0x8023033F
    assert hit.mask_at(10) is None  # its 8 frames are over


def test_track_semantics():
    t = dsa.Track(((3, 5), (6, 7)), loop=True, period=8)
    assert t.value_at(0) == 0xFFFF
    assert t.value_at(3) == 5 and t.value_at(5) == 5 and t.value_at(6) == 7
    assert t.value_at(11) == 5  # wrapped to frame 3
    assert dsa.Track((), False, 0).value_at(4) == 0


def test_mask_helpers():
    groups, parts = dsa.split_mask(0x8023033F)
    assert groups == {0, 1, 2, 3, 4, 5, 8, 9}
    assert parts == {0, 1, 5, 15}
    assert dsa.join_mask(groups, parts) == 0x8023033F


def test_rejects_garbage():
    import pytest

    with pytest.raises(dsa.DsaError):
        dsa.parse(b"NOPE" + bytes(40))


def test_prm_presets():
    from dbkai.formats import prm
    from tests.dsa_fixture import build_presets

    table = prm.parse(build_presets())
    assert table.record_size == 16 and len(table.records) == 3
    presets = prm.visibility_presets(table)
    assert presets[prm.REST_PRESET] == 0x8023033F
    import pytest

    with pytest.raises(prm.PrmError):
        prm.parse(b"nope")


def test_motion_command_segments():
    f = dsa.parse(build_actions(), "fixture.dsa")
    idle = f.actions[0]
    motions = idle.motions
    assert len(motions) == 1
    m = motions[0]
    assert m.period == 30 and [s.resource for s in m.segments] == [0, 1]
    assert idle.motion_at(0) == (0, 1)  # first segment, take frame 1
    assert idle.motion_at(9) == (0, 10)
    assert idle.motion_at(10) == (1, 5)  # second segment starts at take frame 5
    assert idle.motion_at(31) == (0, 2)  # wrapped over the period
    assert f.actions[1].motion_at(0) is None
