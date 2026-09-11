"""The export commands' failure and bookkeeping paths, on synthetic data."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from dbkai.formats import dse
from dbkai.game import GameData
from dbkai.model import scene
from dbkai.nds.rom import NdsRom
from dbkai.ui.exports import LAST_DIR_KEY
from dbkai.ui.main_window import MainWindow
from dbkai.ui.settings import load_str_setting
from tests.dse_fixture import build_model
from tests.test_game import build_rom


def _window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.session._set_model(
        scene.build(dse.parse(build_model()), "fixture"), None, None
    )
    return window


def _choose_folder(monkeypatch, folder):
    monkeypatch.setattr(
        "dbkai.ui.exports.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(folder),
    )


def _record_critical(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *args: shown.append(args[1:]))
    )
    return shown


def test_a_texture_export_that_cannot_write_says_so(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    not_a_folder = tmp_path / "file"
    not_a_folder.write_bytes(b"")
    _choose_folder(monkeypatch, not_a_folder)
    shown = _record_critical(monkeypatch)
    window.exports.textures()
    assert shown and shown[0][0] == "Export failed"


def test_an_export_folder_is_remembered(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    _choose_folder(monkeypatch, tmp_path)
    window.exports.textures()
    assert load_str_setting(LAST_DIR_KEY) == str(tmp_path)


def test_extract_everything_counts_and_leaves_no_dialog(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    window.session.game = GameData(NdsRom(build_rom()))
    _choose_folder(monkeypatch, tmp_path)
    window.exports.extract_all()
    message = window.statusBar().currentMessage()
    assert message.startswith("Extracted ") and "failed" not in message
    done, _of, total = message.split()[1:4]
    assert done == total and int(total) > 0
    assert any(tmp_path.rglob("*.glb"))
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(QProgressDialog) == []


def test_a_cancelled_extraction_says_how_far_it_got(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot)
    window.session.game = GameData(NdsRom(build_rom()))
    _choose_folder(monkeypatch, tmp_path)
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)
    window.exports.extract_all()
    assert window.statusBar().currentMessage().startswith("Extracted 0 of ")
