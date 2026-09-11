"""Hovering a cell too narrow for its text shows the text in full."""

import pytest
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QToolTip, QTreeWidget, QTreeWidgetItem

from dbkai.ui.elided import show_elided_text

#: Far wider than the first column, which the indentation narrows further.
LONG = "a name far too long to fit a column only eighty pixels wide"


@pytest.fixture
def tree(qtbot, monkeypatch):
    widget = QTreeWidget()
    qtbot.addWidget(widget)
    widget.setHeaderLabels(["Name", "Value"])
    widget.setColumnWidth(0, 80)
    show_elided_text(widget)
    widget.resize(400, 200)
    widget.show()
    # What the view asks to show; offscreen, the tooltip itself never is.
    widget.shown = []
    monkeypatch.setattr(
        QToolTip, "showText", lambda _pos, text, *_a: widget.shown.append(text)
    )
    return widget


def _hover(tree: QTreeWidget, item: QTreeWidgetItem, column: int) -> str:
    """The tooltip hovering the cell shows, or ``""``."""
    tree.shown.clear()
    point = tree.visualItemRect(item).topLeft()
    point.setX(tree.header().sectionViewportPosition(column) + 2)
    point += QPoint(0, 2)
    event = QHelpEvent(QEvent.Type.ToolTip, point, tree.viewport().mapToGlobal(point))
    QApplication.sendEvent(tree.viewport(), event)  # through the view's filter
    return "".join(tree.shown)


def test_a_cut_off_cell_shows_its_text(tree):
    item = QTreeWidgetItem([LONG, "short"])
    tree.addTopLevelItem(item)
    assert _hover(tree, item, 0) == LONG


def test_a_cell_that_fits_shows_nothing(tree):
    item = QTreeWidgetItem(["ab", "short"])
    tree.addTopLevelItem(item)
    assert _hover(tree, item, 0) == ""
    assert _hover(tree, item, 1) == ""


def test_a_cells_own_tooltip_is_kept(tree):
    fits = QTreeWidgetItem(["ab", ""])
    fits.setToolTip(0, "its own")
    cut = QTreeWidgetItem([LONG, ""])
    cut.setToolTip(0, "its own")
    holds = QTreeWidgetItem([LONG, ""])
    holds.setToolTip(0, f"/path/{LONG}")
    tree.addTopLevelItems([fits, cut, holds])
    assert _hover(tree, fits, 0) == "its own"
    assert _hover(tree, cut, 0) == f"{LONG}\nits own"
    assert _hover(tree, holds, 0) == f"/path/{LONG}"
