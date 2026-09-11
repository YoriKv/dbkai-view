"""The window builds, the panels follow the session, and exports write files."""

import json
import struct

from PySide6.QtCore import Qt

from dbkai.formats import dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui.main_window import MainWindow
from tests.dse_fixture import build_model, build_motion


def _window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.session._set_model(
        scene.build(dse.parse(build_model()), "fixture"), None, None
    )
    return window


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
        "dbkai.ui.main_window.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    window.export_gltf(True)
    data = target.read_bytes()
    assert data[:4] == b"glTF"
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    assert len(doc["animations"]) == 1
    monkeypatch.setattr(
        "dbkai.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    window.export_textures()
    assert (tmp_path / "skin.png").exists()


def test_per_clip_export_writes_a_folder(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    window.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    monkeypatch.setattr(
        "dbkai.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    window.export_gltf_per_clip()
    assert [p.name for p in tmp_path.iterdir()] == ["fixture__000_spin.glb"]
    assert "1 clip files" in window.statusBar().currentMessage()


def test_actions_panel_lists_and_selects(qtbot):
    from dbkai.formats import dsa
    from tests.dsa_fixture import build_actions

    window = _window(qtbot)
    window.session.action_files = [dsa.parse(build_actions(), "fixture.dsa")]
    window.session.actions_changed.emit()
    tree = window.actions.tree
    assert tree.topLevelItemCount() == 1
    top = tree.topLevelItem(0)
    assert top.childCount() == 2 and top.child(0).text(0) == "1000"
    tree.setCurrentItem(top.child(0))
    assert window.session.action is not None
    assert "0x8023033f" in window.actions.info.text()
    assert window.actions.slider.maximum() == 29
    window.actions.clear.click()
    assert window.session.action is None
