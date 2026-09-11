"""The Parts tab: the game's two draw masks and the viewer's per-mesh
override."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbkai.ui.session import Session

_ROLE = Qt.ItemDataRole.UserRole


class PartsPanel(QWidget):
    """The two game masks (mesh groups and material parts) and a per-mesh
    override, as three checkable branches of one tree. *Rest* restores the
    game's rest preset; the Actions tab drives the masks from a chosen
    action or preset instead."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._building = False
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Meshes"])
        self.tree.setColumnWidth(0, 220)
        buttons = QHBoxLayout()
        self.reset = QPushButton("Rest")
        self.everything = QPushButton("Show all")
        buttons.addWidget(self.reset)
        buttons.addWidget(self.everything)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(buttons)
        layout.addWidget(self.tree)
        self.reset.clicked.connect(session.reset_visibility)
        self.everything.clicked.connect(session.show_everything)
        self.tree.itemChanged.connect(self._changed)
        session.model_changed.connect(self.rebuild)
        session.visibility_changed.connect(self.refresh)

    def rebuild(self) -> None:
        self._building = True
        try:
            self.tree.clear()
            model = self.session.model
            if model is None:
                return
            groups = QTreeWidgetItem(["Groups (mesh slots)", ""])
            parts = QTreeWidgetItem(["Parts (material slots)", ""])
            meshes = QTreeWidgetItem(["Meshes", str(len(model.meshes))])
            self.tree.addTopLevelItems([groups, parts, meshes])
            for g in model.groups:
                members = [m for m in model.meshes if m.group == g]
                names = _distinct(m.name for m in members)
                groups.addChild(
                    _checkable([f"{g}: {names}", str(len(members))], ("group", g))
                )
            for p in model.parts:
                members = [m for m in model.meshes if m.part == p]
                names = _distinct(
                    model.materials[m.material].name
                    for m in members
                    if m.material < len(model.materials)
                )
                parts.addChild(
                    _checkable([f"{p}: {names}", str(len(members))], ("part", p))
                )
            for m in model.meshes:
                bone = (
                    model.skeleton.names[m.bone]
                    if m.bone < len(model.skeleton)
                    else "?"
                )
                item = _checkable(
                    [m.name, f"g{m.group} p{m.part} {bone}"], ("mesh", m.uid)
                )
                item.setToolTip(
                    0,
                    f"{m.vertex_count} vertices, {m.face_count} triangles, bone {bone}"
                    + (", skinned" if m.skinned else "")
                    + (", double-sided" if m.double_sided else ""),
                )
                meshes.addChild(item)
            groups.setExpanded(True)
            parts.setExpanded(True)
        finally:
            self._building = False
        self.refresh()

    def refresh(self) -> None:
        """Tick what the session's visibility shows."""
        vis = self.session.visibility
        self._building = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                for j in range(top.childCount()):
                    item = top.child(j)
                    kind, value = item.data(0, _ROLE)
                    if kind == "group":
                        on = value in vis.groups
                    elif kind == "part":
                        on = value in vis.parts
                    else:
                        on = value not in vis.hidden
                    item.setCheckState(
                        0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                    )
        finally:
            self._building = False

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building or column != 0:
            return
        data = item.data(0, _ROLE)
        if not data:
            return
        kind, value = data
        on = item.checkState(0) == Qt.CheckState.Checked
        if kind == "group":
            self.session.set_group(value, on)
        elif kind == "part":
            self.session.set_part(value, on)
        else:
            self.session.set_mesh_hidden(value, not on)


def _checkable(texts: list[str], data: tuple[str, int]) -> QTreeWidgetItem:
    item = QTreeWidgetItem(texts)
    item.setData(0, _ROLE, data)
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    return item


def _distinct(names: Iterable[str]) -> str:
    """The first four distinct names, in order, with an ellipsis for more."""
    seen = list(dict.fromkeys(names))
    text = ", ".join(seen[:4])
    return text + (", …" if len(seen) > 4 else "")
