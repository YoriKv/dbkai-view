"""Tooltips for cells too narrow for their text: hovering one shows the
text in full."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
)


class ElidedTips(QStyledItemDelegate):
    """Paints as the default delegate does; a cell whose text does not fit
    its column shows that text as its tooltip. A cell with a tooltip of its
    own keeps it, with the full text above it unless it already holds it."""

    def tip(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> str:
        """The tooltip for ``index`` drawn in ``option.rect``."""
        own = index.data(Qt.ItemDataRole.ToolTipRole) or ""
        text = index.data(Qt.ItemDataRole.DisplayRole)
        # The size hint is what the cell needs to draw everything, the text
        # unelided; the view elides it when the cell is narrower.
        if not text or self.sizeHint(option, index).width() <= option.rect.width():
            return own
        text = str(text)
        if not own or text in own:
            return own or text
        return f"{text}\n{own}"

    def helpEvent(
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if event.type() != QEvent.Type.ToolTip or not index.isValid():
            return super().helpEvent(event, view, option, index)
        tip = self.tip(option, index)
        if tip:
            QToolTip.showText(event.globalPos(), tip, view, option.rect)
        else:
            QToolTip.hideText()
            event.ignore()
        return True


def show_elided_text(view: QAbstractItemView) -> None:
    """Give ``view`` the :class:`ElidedTips` delegate."""
    view.setItemDelegate(ElidedTips(view))
