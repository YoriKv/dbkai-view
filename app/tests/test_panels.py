"""The Model dock's panels on their own, over a session with the synthetic
fixture model."""

import pytest
from PySide6.QtCore import Qt

from dbkai.formats import dsa, dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui.panels import ActionsPanel, AnimationPanel
from dbkai.ui.session import Session
from tests.dsa_fixture import build_actions
from tests.dse_fixture import build_model, build_motion


@pytest.fixture
def session(qapp):
    s = Session()
    s._set_model(scene.build(dse.parse(build_model()), "fixture"), None, None)
    return s


def _motion(name: str) -> Motion:
    return Motion(dse.parse(build_motion(frames=3)), name)


def _panel(qtbot, cls, session):
    panel = cls(session)
    qtbot.addWidget(panel)
    # The panel joins after the model loaded; catch up as the window would.
    session.model_changed.emit()
    session.actions_changed.emit()
    return panel


def test_choosing_an_earlier_loaded_motion_binds_it_again(qtbot, session):
    panel = _panel(qtbot, AnimationPanel, session)
    session.set_motion(_motion("spin"))
    session.set_motion(_motion("other"))
    panel.source.setCurrentIndex(panel.source.findText("spin"))
    assert session.motion.name == "spin"
    assert panel.clips.count() == 1


def test_the_clip_list_is_kept_while_the_motion_stays(qtbot, session):
    panel = _panel(qtbot, AnimationPanel, session)
    session.set_motion(_motion("spin"))
    marker = Qt.ItemDataRole.UserRole + 1
    panel.clips.item(0).setData(marker, "kept")
    session.set_clip(session.motion.clips[0])
    assert panel.clips.item(0).data(marker) == "kept"
    assert panel.clips.currentRow() == 0
    session.set_motion(_motion("spin"))  # another motion object: refilled
    assert panel.clips.item(0).data(marker) is None


def test_a_motion_file_that_fails_is_reported(qtbot, session, tmp_path, monkeypatch):
    panel = _panel(qtbot, AnimationPanel, session)
    junk = tmp_path / "junk.dse"
    junk.write_bytes(b"not a dse file at all")
    monkeypatch.setattr(
        "dbkai.ui.panels.animation.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(junk), ""),
    )
    shown = []
    monkeypatch.setattr(
        "dbkai.ui.panels.animation.QMessageBox.critical",
        lambda *args: shown.append(args[1]),
    )
    panel.browse.click()
    assert shown == ["Cannot open file"]


def test_both_transports_follow_the_one_playback(qtbot, session):
    animation = _panel(qtbot, AnimationPanel, session)
    actions = _panel(qtbot, ActionsPanel, session)
    session.set_motion(_motion("spin"))
    animation.play.setChecked(True)
    assert actions.play.isChecked() and actions.play.text() == "Pause"
    session.stop()
    assert not animation.play.isChecked() and animation.play.text() == "Play"


def test_an_action_set_added_again_replaces_the_listed_actions(qtbot, session):
    panel = _panel(qtbot, ActionsPanel, session)
    first = dsa.parse(build_actions(), "fixture.dsa")
    second = dsa.parse(build_actions(), "fixture.dsa")
    session.add_action_set(first)
    session.add_action_set(second)
    tree = panel.tree
    assert tree.topLevelItemCount() == 2
    tree.setCurrentItem(tree.topLevelItem(0))
    assert session.action[0] is second


def test_removing_the_last_action_set_empties_the_list(qtbot, session):
    panel = _panel(qtbot, ActionsPanel, session)
    session.add_action_set(dsa.parse(build_actions(), "fixture.dsa"))
    assert panel.tree.topLevelItemCount() == 2
    session.remove_action_set("fixture.dsa")
    assert panel.source.count() == 0
    assert panel.tree.topLevelItemCount() == 0
    assert not panel.slider.isEnabled()


@pytest.fixture
def fighter(qapp):
    """A session over the fixture ROM, with the hero loaded: its action
    sets and the preset table."""
    from dbkai.game import GameData
    from dbkai.nds.rom import NdsRom
    from tests.test_game import build_rom

    s = Session()
    s.game = GameData(NdsRom(build_rom()))
    s.load_asset(s.game.find("/archiveDBK.dsa/mdl/chr/101100_hero.dse"))
    return s


def test_a_preset_is_chosen_like_an_action(qtbot, fighter):
    panel = _panel(qtbot, ActionsPanel, fighter)
    panel.show()
    assert panel.source.currentText() == "100000_NORMAL_BALANCE.dsa"
    assert panel.source.itemText(panel.source.count() - 1) == "Presets"
    assert not panel.tree.isColumnHidden(1) and panel.transport.frame_row.isVisible()
    panel.source.setCurrentIndex(panel.source.count() - 1)
    tree = panel.tree
    assert [tree.topLevelItem(i).text(0) for i in range(3)] == [
        "10000",
        "10005",
        "11000",
    ]
    assert tree.isColumnHidden(1) and tree.headerItem().text(2) == "Shows"
    assert not panel.transport.frame_row.isVisible()
    tree.setCurrentItem(tree.topLevelItem(2))
    assert fighter.preset == 11000 and fighter.action is None
    assert tree.currentItem() is tree.topLevelItem(2)  # the choice stays shown
    assert panel.info.text().startswith("preset 11000: mask 0x804300ff")
    panel.clear.click()
    assert fighter.preset is None and tree.currentItem() is None
    # Choosing an action lists its set again, with the frames and slider.
    tree.setCurrentItem(tree.topLevelItem(0))
    fighter.set_action((fighter.action_sets[0], fighter.action_sets[0].actions[0]))
    assert panel.source.currentText() == "100000_NORMAL_BALANCE.dsa"
    assert not tree.isColumnHidden(1) and panel.transport.frame_row.isVisible()
    assert tree.currentItem() is tree.topLevelItem(0)
    # And a preset chosen elsewhere brings the presets back.
    fighter.set_preset(10005)
    assert panel.source.currentText() == "Presets"
    assert tree.currentItem().text(0) == "10005"
    fighter.set_group(1, False)  # set by hand: no longer the preset
    assert tree.currentItem() is None
