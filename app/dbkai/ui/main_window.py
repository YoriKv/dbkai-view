"""The main window: the ROM browser on the left, the viewport in the middle,
the model panels on the right, and the menus that open and export things.
The export commands themselves are :class:`~dbkai.ui.exports.Exports`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dbkai import APP_NAME, __version__
from dbkai.ui.asset_tree import AssetTree
from dbkai.ui.exports import LAST_DIR_KEY, Exports
from dbkai.ui.panels import (
    ActionsPanel,
    AnimationPanel,
    MaterialsPanel,
    PartsPanel,
    SkeletonPanel,
)
from dbkai.ui.session import Session
from dbkai.ui.settings import (
    load_bool_setting,
    load_bytes_setting,
    load_enum_setting,
    load_str_setting,
    save_bool_setting,
    save_bytes_setting,
    save_enum_setting,
    save_str_setting,
)
from dbkai.ui.shortcuts import ShortcutGuide, shortcut_sections
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme
from dbkai.ui.viewport import Viewport

log = logging.getLogger(__name__)

GEOMETRY_KEY = "window/geometry"
STATE_KEY = "window/state"
LAST_ROM_KEY = "file/last_rom"

_THEME_ENTRIES = {
    Theme.LIGHT: "&Light",
    Theme.DARK: "&Dark",
}

#: View toggles: (option name on the session, menu label, key, setting key,
#: default). The keys are bare letters, as a 3D viewer's usually are; a text
#: box that has focus keeps them for typing.
_VIEW_TOGGLES = [
    ("textures", "&Textures", "T", "view/textures", True),
    ("vertex_colors", "Vertex &colours", "C", "view/vertex_colors", True),
    ("culling", "&Back-face culling", "B", "view/culling", True),
    ("wireframe", "&Wireframe", "W", "view/wireframe", False),
    ("grid", "&Grid", "G", "view/grid", True),
    ("bones", "&Skeleton", "S", "view/bones", False),
]

_ROM_FILTER = "DS ROM (*.nds);;All files (*)"
_DSE_FILTER = "DSE files (*.dse *.dsez *.dse7);;All files (*)"


class MainWindow(QMainWindow):
    """The top-level window. Owns the session, the menus and the docks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.session = Session(self)
        self.session.status.connect(self._status)
        self.exports = Exports(self, self.session, self._status)

        self.viewport = Viewport(self.session)
        self.setCentralWidget(self.viewport)

        self.assets = AssetTree(self.session)
        self.assets_dock = self._dock(
            "Assets", "assetsDock", self.assets, Qt.DockWidgetArea.LeftDockWidgetArea
        )

        self.parts = PartsPanel(self.session)
        self.animation = AnimationPanel(self.session)
        self.actions = ActionsPanel(self.session)
        self.materials = MaterialsPanel(self.session)
        self.skeleton = SkeletonPanel(self.session)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.parts, "Parts")
        self.tabs.addTab(self.animation, "Animation")
        self.tabs.addTab(self.actions, "Actions")
        self.tabs.addTab(self.materials, "Materials")
        self.tabs.addTab(self.skeleton, "Skeleton")
        #: The open model's name, over the tabs.
        self.model_name = QLabel()
        self.model_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        font = self.model_name.font()
        font.setBold(True)
        self.model_name.setFont(font)
        model_panel = QWidget()
        layout = QVBoxLayout(model_panel)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.addWidget(self.model_name)
        layout.addWidget(self.tabs, 1)
        self.model_dock = self._dock(
            "Model", "modelDock", model_panel, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.session.model_changed.connect(self._show_model_name)
        self._show_model_name()

        self.theme = self._build_menus()
        self.statusBar().showMessage("Open a ROM (File > Open ROM) or a DSE file.")
        self.resizeDocks(
            [self.assets_dock, self.model_dock], [340, 380], Qt.Orientation.Horizontal
        )
        self._restore_layout()

    def _dock(
        self, title: str, name: str, widget: QWidget, area: Qt.DockWidgetArea
    ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    # -- menus -------------------------------------------------------------

    def _build_menus(self) -> QActionGroup:
        """Fill the menu bar; returns the theme's action group."""
        bar = self.menuBar()
        exports = self.exports

        file_menu = bar.addMenu("&File")
        self._action(
            file_menu, "Open &ROM…", self.open_rom, QKeySequence.StandardKey.Open
        )
        self._action(file_menu, "Open &File…", self.open_file, "Ctrl+Shift+O")
        file_menu.addSeparator()
        self._action(
            file_menu,
            "Export &glTF (current clip)…",
            lambda: exports.gltf(every_clip=False),
            "Ctrl+E",
        )
        self._action(
            file_menu,
            "Export glTF (&all clips)…",
            lambda: exports.gltf(every_clip=True),
        )
        self._action(
            file_menu, "Export glTF (one file per c&lip)…", exports.gltf_per_clip
        )
        self._action(file_menu, "Export glTF (current a&ction)…", exports.gltf_action)
        self._action(file_menu, "Export &Textures…", exports.textures)
        self._action(file_menu, "Extract &Everything…", exports.extract_all)
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        view_menu = bar.addMenu("&View")
        for name, label, shortcut, key, default in _VIEW_TOGGLES:
            on = load_bool_setting(key, default)
            self.session.set_option(name, on)
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(on)
            action.setShortcut(QKeySequence(shortcut))
            action.toggled.connect(
                lambda on, name=name, key=key: self._toggle(name, key, on)
            )
            view_menu.addAction(action)
        view_menu.addSeparator()
        self._action(view_menu, "&Reset camera", self.viewport.reset_camera, "Home")
        self._action(view_menu, "&Play / Pause", self.session.toggle_play, "Space")
        view_menu.addSeparator()
        for dock, label, shortcut in (
            (self.assets_dock, "&Assets panel", "Ctrl+1"),
            (self.model_dock, "&Model panel", "Ctrl+2"),
        ):
            toggle = dock.toggleViewAction()
            toggle.setText(label)
            toggle.setShortcut(QKeySequence(shortcut))
            view_menu.addAction(toggle)
        view_menu.addSeparator()
        theme = exclusive(
            view_menu.addMenu("&Theme"),
            self,
            _THEME_ENTRIES,
            load_enum_setting(THEME_KEY, Theme.LIGHT),
            self.set_theme,
        )

        # Last, so the shortcut guide, which reads the menu bar, sees every
        # other menu.
        help_menu = bar.addMenu("&Help")
        self._action(
            help_menu,
            "&Shortcuts…",
            self.show_shortcuts,
            QKeySequence.StandardKey.HelpContents,  # F1
        )
        help_menu.addSeparator()
        self._action(help_menu, f"&About {APP_NAME}", self.show_about)
        return theme

    def _action(
        self,
        menu: QMenu,
        label: str,
        slot: Callable[[], object],
        shortcut: QKeySequence.StandardKey | str | None = None,
    ) -> QAction:
        action = QAction(label, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        # Called with no arguments: ``triggered`` carries a ``checked`` flag,
        # which would otherwise land in the slot's first parameter (a path).
        action.triggered.connect(lambda: slot())
        menu.addAction(action)
        return action

    def _toggle(self, name: str, key: str, on: bool) -> None:
        self.session.set_option(name, on)
        save_bool_setting(key, on)

    def set_theme(self, theme: Theme) -> None:
        apply_theme(theme)
        save_enum_setting(THEME_KEY, theme)

    def show_shortcuts(self) -> None:
        ShortcutGuide(shortcut_sections(self), self).exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>"
            "A model extractor and viewer for DB Kai: Ultimate Butoden.",
        )

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _show_model_name(self) -> None:
        model = self.session.model
        self.model_name.setText(model.name if model is not None else "No model")
        self.model_name.setToolTip(
            str(self.session.model_path or "") if model is not None else ""
        )

    # -- opening -------------------------------------------------------------

    def open_rom(self, path: str | None = None) -> None:
        """Open a ROM, asking for one when no ``path`` is given."""
        path = path or self._ask_open("Open ROM", _ROM_FILTER)
        if path and self._open(path, self.session.open_rom, "Cannot open ROM"):
            save_str_setting(LAST_ROM_KEY, str(path))

    def open_file(self, path: str | None = None) -> None:
        """Open a model or motion file, asking for one when no ``path`` is
        given."""
        path = path or self._ask_open("Open file", _DSE_FILTER)
        if path:
            self._open(path, self.session.open_file, "Cannot open file")

    def _ask_open(self, title: str, filters: str) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self, title, load_str_setting(LAST_DIR_KEY), filters
        )
        return path

    def _open(self, path: str, opener: Callable[[str], None], failure: str) -> bool:
        """Open ``path`` with ``opener`` and remember its folder; a failure
        is shown under the title ``failure`` and returns ``False``."""
        try:
            opener(path)
        except Exception as exc:  # noqa: BLE001 - reported to the person
            log.exception("opening %s", path)
            QMessageBox.critical(self, failure, f"{path}\n\n{exc}")
            return False
        save_str_setting(LAST_DIR_KEY, str(Path(path).parent))
        return True

    # -- layout persistence ------------------------------------------------

    def _restore_layout(self) -> None:
        geometry = load_bytes_setting(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = load_bytes_setting(STATE_KEY)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.session.stop()
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
    """Fill ``menu`` with one checkable action per entry, exclusive, with
    ``current`` checked; checking another calls ``apply`` with its member.

    "Pick exactly one" is what makes an exclusive QActionGroup the right shape:
    checking one unchecks the rest with no bookkeeping in the window.
    ``current`` is only shown, not applied: a stored preference is already in
    effect by the time the window is built (:func:`dbkai.app.main` applies the
    theme first, so nothing is styled twice).
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
    return group
