"""Help > Shortcuts: every key the viewer answers to, in one page.

The page is **generated from the live menu bar** rather than a hand-written
table: every key in the app is a ``QAction`` shortcut on a menu, so walking the
menus keeps the page correct for free. A new action with a key shows up here
without anyone remembering to add it. What no menu can hold is the viewport's
mouse gestures, appended as the one hand-maintained section.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dbkai import APP_NAME

__all__ = ["ShortcutGuide", "shortcut_sections"]

#: A section: its title and its ``(what, keys)`` rows.
type Section = tuple[str, list[tuple[str, str]]]

#: The mouse gestures of the viewport (:mod:`dbkai.ui.viewport`). No menu
#: action can express a drag, so this table is maintained by hand.
VIEWPORT_GESTURES: tuple[tuple[str, str], ...] = (
    ("Orbit", "Drag"),
    ("Pan", "Right-drag or middle-drag"),
    ("Zoom", "Scroll"),
)


def _key_text(action: QAction) -> str:
    """The primary key of ``action`` as the platform spells it, or ``""``."""
    return action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)


def _label_text(action: QAction) -> str:
    # "&" marks the mnemonic, so it is not part of the name; neither is the
    # ellipsis that says a dialog follows.
    return action.text().replace("&", "").rstrip("…").strip()


def _menu_entries(menu: QMenu) -> list[tuple[str, str]]:
    """Every ``(label, keys)`` pair in ``menu``, submenus flattened in place.
    Actions with no key are dropped: the menu itself is their documentation."""
    entries: list[tuple[str, str]] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        submenu = action.menu()
        if submenu is not None:
            entries.extend(_menu_entries(submenu))
        elif keys := _key_text(action):
            entries.append((_label_text(action), keys))
    return entries


def shortcut_sections(window: QMainWindow) -> list[Section]:
    """The page's contents: one section per menu, then the viewport. Separate
    from the dialog so it can be tested without opening a modal."""
    sections: list[Section] = []
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is None:
            continue
        if entries := _menu_entries(menu):
            sections.append((_label_text(action), entries))
    sections.append(("Viewport", list(VIEWPORT_GESTURES)))
    return sections


def _section_widget(title: str, entries: list[tuple[str, str]]) -> QWidget:
    """One titled two-column section: names on the left, keys on the right."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    heading = QLabel(title)
    font = heading.font()
    font.setBold(True)
    heading.setFont(font)
    layout.addWidget(heading)
    rule = QFrame()
    rule.setFrameShape(QFrame.Shape.HLine)
    rule.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(rule)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(1)
    grid.setColumnStretch(0, 1)
    for row, (name, keys) in enumerate(entries):
        grid.addWidget(QLabel(name), row, 0)
        key_label = QLabel(keys)
        key_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(key_label, row, 1)
    layout.addLayout(grid)
    return box


def _balanced_columns(sections: list[Section], count: int = 2) -> list[list[Section]]:
    """Split sections across ``count`` columns of even height. Sections stay
    whole and in order; each goes to whichever column is shortest so far."""
    columns: list[list[Section]] = [[] for _ in range(count)]
    heights = [0] * count
    for section in sections:
        target = heights.index(min(heights))
        columns[target].append(section)
        heights[target] += len(section[1]) + 2  # rows plus the heading and rule
    return columns


class ShortcutGuide(QDialog):
    """Help > Shortcuts: the sections side by side, scrolled if they outgrow
    the screen."""

    def __init__(self, sections: list[Section], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - Shortcuts")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(12, 12, 12, 12)
        columns.setSpacing(28)
        for column in _balanced_columns(sections):
            lane = QVBoxLayout()
            lane.setSpacing(14)
            for title, entries in column:
                lane.addWidget(_section_widget(title, entries))
            lane.addStretch(1)
            columns.addLayout(lane)

        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        # Sized to the width the columns need, since the page is generated and
        # a new key anywhere can widen a column; the height is a starting size.
        margins = layout.contentsMargins()
        width = (
            body.sizeHint().width()
            + scroll.verticalScrollBar().sizeHint().width()
            + 2 * scroll.frameWidth()
            + margins.left()
            + margins.right()
        )
        height = body.sizeHint().height() + buttons.sizeHint().height() + 40
        available = self.screen().availableGeometry()
        self.resize(min(width, available.width()), min(height, available.height()))
