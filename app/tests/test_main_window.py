"""The window builds, the panels follow the session, and exports write files."""

import json
import struct

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from dbkai.formats import dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui.main_window import MainWindow
from dbkai.ui.shortcuts import VIEWPORT_GESTURES, ShortcutGuide, shortcut_sections
from tests.dse_fixture import build_model, build_motion


def _window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.session._set_model(
        scene.build(dse.parse(build_model()), "fixture"), None, None
    )
    return window


def _activate(qtbot, window):
    """Shortcuts only fire in the active window, which offscreen needs told."""
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)


def _menu(window, title):
    return next(a.menu() for a in window.menuBar().actions() if a.text() == title)


def test_the_model_dock_names_the_model(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.model_name.text() == "No model"
    window.session._set_model(
        scene.build(dse.parse(build_model()), "fixture"), None, None
    )
    assert window.model_name.text() == "fixture"


def test_space_plays_and_pauses_wherever_the_focus_is(qtbot):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    _activate(qtbot, window)
    window.assets.tree.setFocus()
    QTest.keyClick(window.assets.tree, Qt.Key.Key_Space)
    assert window.session.playing
    QTest.keyClick(window.assets.tree, Qt.Key.Key_Space)
    assert not window.session.playing


def test_space_in_the_parts_tree_toggles_the_row_instead_of_playing(qtbot):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    _activate(qtbot, window)
    tree = window.parts.tree
    tree.setFocus()
    group = tree.topLevelItem(0).child(0)
    tree.setCurrentItem(group)
    was = group.checkState(0)
    QTest.keyClick(tree, Qt.Key.Key_Space)
    assert group.checkState(0) != was
    assert not window.session.playing
    # A heading has no box, so there Space still means Play / Pause.
    tree.setCurrentItem(tree.topLevelItem(0))
    QTest.keyClick(tree, Qt.Key.Key_Space)
    assert window.session.playing


def test_view_letters_toggle_the_view_but_type_into_the_filter(qtbot):
    window = _window(qtbot)
    _activate(qtbot, window)
    window.viewport.setFocus()
    QTest.keyClick(window.viewport, Qt.Key.Key_W)
    assert window.session.options.wireframe
    window.assets.filter.setFocus()
    QTest.keyClick(window.assets.filter, Qt.Key.Key_W)
    assert window.assets.filter.text() == "w" and window.session.options.wireframe


def test_help_shortcuts_lists_every_key_from_the_menus(qtbot):
    window = _window(qtbot)
    for action in _menu(window, "&Help").actions():
        if not action.isSeparator():
            action.trigger()  # conftest stops the guide's exec() from blocking
    assert window.findChildren(ShortcutGuide)
    sections = dict(shortcut_sections(window))
    native = QKeySequence.SequenceFormat.NativeText
    assert dict(sections["File"])["Open ROM"] == QKeySequence(
        QKeySequence.StandardKey.Open
    ).toString(native)
    view = dict(sections["View"])
    assert view["Wireframe"] == "W" and view["Play / Pause"] == "Space"
    assert view["Assets panel"] == QKeySequence("Ctrl+1").toString(native)
    assert "Light" not in view  # the theme entries carry no key
    assert dict(sections["Help"])["Shortcuts"] == QKeySequence(
        QKeySequence.StandardKey.HelpContents
    ).toString(native)
    assert sections["Viewport"] == list(VIEWPORT_GESTURES)
    assert all(name and keys for rows in sections.values() for name, keys in rows)


def test_panels_follow_the_model(qtbot):
    window = _window(qtbot)
    parts = window.parts.tree
    assert parts.topLevelItemCount() == 3
    groups = parts.topLevelItem(0)
    assert groups.childCount() == 2
    assert groups.child(1).checkState(0) == Qt.CheckState.Checked
    groups.child(1).setCheckState(0, Qt.CheckState.Unchecked)
    assert window.session.visibility.groups == {0}
    meshes = parts.topLevelItem(2)
    meshes.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert window.session.visibility.hidden == {0}
    assert window.skeleton.tree.topLevelItemCount() == 1
    assert window.skeleton.tree.topLevelItem(0).child(0).text(0) == "tip"
    assert window.materials.textures.count() == 1
    assert window.materials.materials.topLevelItem(0).text(3) == "STt"


def test_animation_panel_lists_clips(qtbot):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    panel = window.animation
    assert panel.clips.count() == 1 and panel.clips.currentRow() == 0
    assert panel.slider.maximum() == 2
    panel.slider.setValue(2)
    assert window.session.frame == 2
    assert panel.frame_label.text() == "3 / 3"
    panel.play.setChecked(True)
    assert window.session.playing
    panel.play.setChecked(False)
    assert not window.session.playing
    panel.slider.setValue(2)
    panel.play.setChecked(True)  # Play restarts from the first frame
    assert window.session.frame == 0 and panel.frame_label.text() == "1 / 3"
    panel.play.setChecked(False)
    assert panel.bones_label.text() == "2 of 2 matched"


def test_animation_panel_survives_a_motion_outside_the_choices(qtbot):
    # A motion bound from disk or by an action is not in the source list, so
    # the panel adds an entry for it; rebuilding again must not choke on it.
    window = _window(qtbot)
    panel = window.animation
    for name in ["spin", "spin", "other"]:
        window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), name))
        assert panel.source.currentText() == name
    assert panel.source.count() == 3  # bind pose, spin, other
    panel.source.setCurrentIndex(0)
    assert window.session.motion is None


def test_view_toggles_reach_the_session(qtbot):
    window = _window(qtbot)
    view = window.menuBar().actions()[1].menu()
    wire = next(a for a in view.actions() if a.text() == "&Wireframe")
    wire.setChecked(True)
    assert window.session.options.wireframe


def test_exports_write_files(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    target = tmp_path / "out.glb"
    monkeypatch.setattr(
        "dbkai.ui.exports.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    window.exports.gltf(every_clip=True)
    data = target.read_bytes()
    assert data[:4] == b"glTF"
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    assert len(doc["animations"]) == 1
    monkeypatch.setattr(
        "dbkai.ui.exports.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    window.exports.textures()
    assert (tmp_path / "skin.png").exists()


def test_per_clip_export_writes_a_folder(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    monkeypatch.setattr(
        "dbkai.ui.exports.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    window.exports.gltf_per_clip()
    assert [p.name for p in tmp_path.iterdir()] == ["fixture__000_spin.glb"]
    assert "1 clip files" in window.statusBar().currentMessage()


def test_action_export_writes_the_action(qtbot, tmp_path, monkeypatch):
    from dbkai.formats import dsa
    from tests.dsa_fixture import build_actions

    window = _window(qtbot)
    file = dsa.parse(build_actions(), "fixture.dsa")
    window.session.add_action_set(file)
    window.session.set_action((file, file.actions[1]))
    suggested = []

    def save(*args, **_kwargs):
        suggested.append(args[2])
        return str(tmp_path / "out.glb"), ""

    monkeypatch.setattr("dbkai.ui.exports.QFileDialog.getSaveFileName", save)
    window.exports.gltf_action()
    assert suggested[0].endswith("fixture__fixture_2000.glb")
    data = (tmp_path / "out.glb").read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    assert [a["name"] for a in doc["animations"]] == ["fixture_2000"]
    assert "KHR_node_visibility" in doc["extensionsUsed"]


def test_actions_panel_lists_and_selects(qtbot):
    from dbkai.formats import dsa
    from tests.dsa_fixture import build_actions

    window = _window(qtbot)
    window.session.action_sets = [dsa.parse(build_actions(), "fixture.dsa")]
    window.session.actions_changed.emit()
    panel = window.actions
    assert panel.source.count() == 1 and panel.source.currentText() == "fixture.dsa"
    tree = panel.tree
    assert tree.topLevelItemCount() == 2 and tree.topLevelItem(0).text(0) == "1000"
    tree.setCurrentItem(tree.topLevelItem(0))
    assert window.session.action is not None
    assert "0x8023033f" in panel.info.text()
    assert panel.slider.maximum() == 29
    # Switching while playing keeps playing, as the Animation tab does.
    panel.play.setChecked(True)
    assert window.session.playing
    tree.setCurrentItem(tree.topLevelItem(1))
    assert window.session.action[1].action_id == 2000 and window.session.playing
    panel.clear.click()
    assert window.session.action is None and not window.session.playing
    assert tree.currentItem() is None
    # A file set by hand counts as added, so the Remove button takes it out.
    assert panel.remove.isEnabled()
    panel.remove.click()
    assert window.session.action_sets == [] and panel.source.count() == 0


def test_an_action_set_opened_before_any_model_lists_without_a_crash(qtbot):
    from dbkai.formats import dsa
    from tests.dsa_fixture import build_actions

    window = MainWindow()
    qtbot.addWidget(window)
    window.session.add_action_set(dsa.parse(build_actions(), "fixture.dsa"))
    panel = window.actions
    assert panel.source.currentText() == "fixture.dsa"
    assert panel.remove.isEnabled()


def test_picking_a_clip_source_drops_the_action_in_the_actions_tab(qtbot):
    from dbkai.formats import dsa
    from tests.dsa_fixture import build_actions

    window = _window(qtbot)
    file = dsa.parse(build_actions(), "fixture.dsa")
    window.session.add_action_set(file)
    window.actions.tree.setCurrentItem(window.actions.tree.topLevelItem(0))
    assert window.actions.slider.isEnabled()
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    assert window.actions.tree.currentItem() is None
    assert not window.actions.slider.isEnabled()


def test_a_menu_command_is_called_without_the_checked_flag(qtbot, monkeypatch):
    window = _window(qtbot)
    asked = []
    monkeypatch.setattr(
        "dbkai.ui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: asked.append(a[1]) or ("", ""),
    )
    file_menu = _menu(window, "&File")
    next(a for a in file_menu.actions() if a.text() == "Open &ROM…").trigger()
    assert asked == ["Open ROM"]
