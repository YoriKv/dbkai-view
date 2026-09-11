"""The main window: menus, the theme switch, and an empty viewport to fill."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QMessageBox, QWidget

from dbkai import APP_NAME, __version__
from dbkai.ui.settings import (
    load_bytes_setting,
    load_enum_setting,
    save_bytes_setting,
    save_enum_setting,
)
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme

GEOMETRY_KEY = "window/geometry"
STATE_KEY = "window/state"

_THEME_ENTRIES = {
    Theme.LIGHT: "&Light",
    Theme.DARK: "&Dark",
}


class MainWindow(QMainWindow):
    """The top-level window. Owns the menus and the viewport placeholder."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)

        placeholder = QLabel("No model loaded")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setEnabled(False)
        self.setCentralWidget(placeholder)

        self.theme = self._build_menus()
        self.statusBar()
        self._restore_layout()

    # -- menus -------------------------------------------------------------

    def _build_menus(self) -> QActionGroup:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = bar.addMenu("&View")
        theme = exclusive(
            view_menu.addMenu("&Theme"),
            self,
            _THEME_ENTRIES,
            load_enum_setting(THEME_KEY, Theme.LIGHT),
            self.set_theme,
        )

        help_menu = bar.addMenu("&Help")
        about = QAction(f"&About {APP_NAME}", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)
        return theme

    def set_theme(self, theme: Theme) -> None:
        apply_theme(theme)
        save_enum_setting(THEME_KEY, theme)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>"
            "A model extractor and viewer for DB Kai: Ultimate Butoden.",
        )

    # -- layout persistence ------------------------------------------------

    def _restore_layout(self) -> None:
        geometry = load_bytes_setting(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = load_bytes_setting(STATE_KEY)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        save_bytes_setting(GEOMETRY_KEY, self.saveGeometry())
        save_bytes_setting(STATE_KEY, self.saveState())
        super().closeEvent(event)


def exclusive[E: Enum](
    menu: QMenu,
    window: QMainWindow,
    entries: dict[E, str],
    current: E,
    apply: Callable[[E], None],
) -> QActionGroup:
    """Fill ``menu`` with one checkable action per entry, exclusive, and put
    ``current`` into effect.

    "Pick exactly one" is what makes an exclusive QActionGroup the right shape:
    checking one unchecks the rest with no bookkeeping in the window.
    """
    group = QActionGroup(window)
    group.setExclusive(True)
    group.triggered.connect(lambda chosen: apply(chosen.data()))
    for member, label in entries.items():
        made = QAction(label, window)
        made.setCheckable(True)
        made.setChecked(member is current)
        made.setData(member)
        group.addAction(made)
        menu.addAction(made)
    # A stored preference has to take effect as well as show as checked; nothing
    # else applies it, since no action was triggered to get here.
    apply(current)
    return group
