"""The Assets dock: the tree of a ROM's assets and what activating one does."""

from PySide6.QtWidgets import QMessageBox

from dbkai.game import GameData
from dbkai.nds.rom import NdsRom
from dbkai.ui.asset_tree import AssetTree
from dbkai.ui.session import Session
from tests.test_game import build_rom


def _tree(qtbot):
    session = Session()
    tree = AssetTree(session)
    qtbot.addWidget(tree)
    session.game = GameData(NdsRom(build_rom()))
    session.game_changed.emit()
    return session, tree


def _item(tree, path):
    return next(item for item, asset in tree._items if asset.path == path)


def test_activating_a_model_loads_it(qtbot):
    session, tree = _tree(qtbot)
    item = _item(tree, "/archiveDBK.dsa/mdl/chr/101100_hero.dse")
    tree.tree.itemActivated.emit(item, 0)
    assert session.model is not None and session.model.name == "101100_hero.dse"


def test_an_asset_that_fails_to_load_is_reported(qtbot, monkeypatch):
    session, tree = _tree(qtbot)

    def broken(_asset):
        raise ValueError("not a DSE file")

    monkeypatch.setattr(session, "load_asset", broken)
    shown = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *args: shown.append(args[1:]))
    )
    tree.tree.itemActivated.emit(_item(tree, "/debug/cube.dse7"), 0)
    assert shown and shown[0][0] == "Cannot open asset"
    assert "not a DSE file" in shown[0][1]


def test_the_filter_hides_what_does_not_match_and_empty_folders(qtbot):
    _session, tree = _tree(qtbot)
    tree.filter.setText("TALL_POWER")
    shown = [a.path for item, a in tree._items if not item.isHidden()]
    assert shown == ["/debug/110000_TALL_POWER.dsa"]
    top = [tree.tree.topLevelItem(i) for i in range(tree.tree.topLevelItemCount())]
    assert [t.text(0) for t in top if not t.isHidden()] == ["debug"]
