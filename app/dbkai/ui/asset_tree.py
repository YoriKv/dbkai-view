"""The ROM browser: every model, motion, texture set and action set in the
cartridge, as a tree of its paths, with a filter box."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLineEdit,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbkai.game import Asset
from dbkai.ui.elided import show_elided_text
from dbkai.ui.session import Session

log = logging.getLogger(__name__)

_ASSET_ROLE = Qt.ItemDataRole.UserRole


class AssetTree(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (name or path)")
        self.filter.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Kind", "Size"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setUniformRowHeights(True)
        show_elided_text(self.tree)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter)
        layout.addWidget(self.tree)
        self.filter.textChanged.connect(self._apply_filter)
        self.tree.itemActivated.connect(self._activated)
        session.game_changed.connect(self.rebuild)
        self._items: list[tuple[QTreeWidgetItem, Asset]] = []

    def rebuild(self) -> None:
        self.tree.clear()
        self._items = []
        game = self.session.game
        if game is None:
            return
        folders: dict[str, QTreeWidgetItem] = {}

        def folder(path: str) -> QTreeWidgetItem | None:
            if path in ("", "/"):
                return None
            if path not in folders:
                parent = folder(path.rsplit("/", 1)[0])
                item = QTreeWidgetItem([path.rsplit("/", 1)[-1] or path, "", ""])
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                folders[path] = item
            return folders[path]

        for asset in game.assets:
            parent = folder(asset.directory)
            item = QTreeWidgetItem([asset.name, asset.kind.value, _size(asset.size)])
            item.setData(0, _ASSET_ROLE, asset)
            item.setToolTip(0, asset.path)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._items.append((item, asset))
        self.tree.expandToDepth(1)
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for item, asset in self._items:
            item.setHidden(bool(text) and text not in asset.path.lower())
        # A folder shows when any child does.
        for i in range(self.tree.topLevelItemCount()):
            _prune(self.tree.topLevelItem(i))

    def _activated(self, item: QTreeWidgetItem, _column: int) -> None:
        asset = item.data(0, _ASSET_ROLE)
        if asset is None:
            return
        try:
            self.session.load_asset(asset)
        except Exception as exc:  # noqa: BLE001 - reported to the person
            log.exception("loading %s", asset.path)
            QMessageBox.critical(self, "Cannot open asset", f"{asset.path}\n\n{exc}")

    def select_asset(self, asset: Asset) -> None:
        for item, a in self._items:
            if a is asset:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return


def _prune(item: QTreeWidgetItem) -> bool:
    """Hide a folder whose children are all hidden; returns whether shown."""
    if item.childCount() == 0:
        return not item.isHidden()
    shown = False
    for i in range(item.childCount()):
        shown = _prune(item.child(i)) or shown
    item.setHidden(not shown)
    return shown


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
