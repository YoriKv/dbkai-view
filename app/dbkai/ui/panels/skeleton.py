"""The Skeleton tab: the bone hierarchy with rest positions."""

from __future__ import annotations

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from dbkai.ui.elided import show_elided_text
from dbkai.ui.session import Session


class SkeletonPanel(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bone", "Rest position"])
        self.tree.setColumnWidth(0, 200)
        show_elided_text(self.tree)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        session.model_changed.connect(self.rebuild)

    def rebuild(self) -> None:
        self.tree.clear()
        model = self.session.model
        if model is None:
            return
        sk = model.skeleton
        items: dict[int, QTreeWidgetItem] = {}
        for i in sk.order:
            t = sk.bind_world[i][:3, 3]
            item = QTreeWidgetItem([sk.names[i], f"{t[0]:.2f}, {t[1]:.2f}, {t[2]:.2f}"])
            p = sk.parents[i]
            if p in items:
                items[p].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            items[i] = item
        self.tree.expandAll()
