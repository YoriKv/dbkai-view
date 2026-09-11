"""The main window: the ROM browser on the left, the viewport in the middle,
the model panels on the right, and the menus that open and export things."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QWidget,
)

from dbkai import APP_NAME, __version__
from dbkai.export.gltf import export_clips, export_glb
from dbkai.export.png import encode_png
from dbkai.game import AssetKind
from dbkai.model.animation import BoundMotion, Clip
from dbkai.ui.asset_tree import AssetTree
from dbkai.ui.panels import AnimationPanel, MaterialsPanel, PartsPanel, SkeletonPanel
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
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme
from dbkai.ui.viewport import Viewport

log = logging.getLogger(__name__)

GEOMETRY_KEY = "window/geometry"
STATE_KEY = "window/state"
LAST_ROM_KEY = "file/last_rom"
LAST_DIR_KEY = "file/last_dir"

_THEME_ENTRIES = {
    Theme.LIGHT: "&Light",
    Theme.DARK: "&Dark",
}

#: View toggles: (option name on the session, menu label, setting key, default).
_VIEW_TOGGLES = [
    ("textures", "&Textures", "view/textures", True),
    ("vertex_colors", "Vertex &colours", "view/vertex_colors", True),
    ("culling", "&Back-face culling", "view/culling", True),
    ("wireframe", "&Wireframe", "view/wireframe", False),
    ("grid", "&Grid", "view/grid", True),
    ("bones", "&Skeleton", "view/bones", False),
]

_DSE_FILTER = "DSE files (*.dse *.dsez *.dse7);;All files (*)"


class MainWindow(QMainWindow):
    """The top-level window. Owns the session, the menus and the docks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.session = Session(self)
        self.session.status.connect(self._status)

        self.viewport = Viewport(self.session)
        self.setCentralWidget(self.viewport)

        self.assets = AssetTree(self.session)
        self.assets_dock = self._dock(
            "Assets", "assetsDock", self.assets, Qt.DockWidgetArea.LeftDockWidgetArea
        )

        self.parts = PartsPanel(self.session)
        self.animation = AnimationPanel(self.session)
        self.materials = MaterialsPanel(self.session)
        self.skeleton = SkeletonPanel(self.session)
        tabs = QTabWidget()
        tabs.addTab(self.parts, "Parts")
        tabs.addTab(self.animation, "Animation")
        tabs.addTab(self.materials, "Materials")
        tabs.addTab(self.skeleton, "Skeleton")
        self.model_dock = self._dock(
            "Model", "modelDock", tabs, Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.theme = self._build_menus()
        self.statusBar().showMessage("Open a ROM (File > Open ROM) or a DSE file.")
        self.resizeDocks(
            [self.assets_dock, self.model_dock], [340, 380], Qt.Orientation.Horizontal
        )
        self._restore_layout()
        for name, _label, key, default in _VIEW_TOGGLES:
            self.session.set_option(name, load_bool_setting(key, default))

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
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action(
            file_menu, "Open &ROM…", self.open_rom, QKeySequence.StandardKey.Open
        )
        self._action(file_menu, "Open &File…", self.open_file, "Ctrl+Shift+O")
        file_menu.addSeparator()
        self._action(
            file_menu,
            "Export &glTF (current clip)…",
            lambda: self.export_gltf(False),
            "Ctrl+E",
        )
        self._action(
            file_menu, "Export glTF (&all clips)…", lambda: self.export_gltf(True)
        )
        self._action(
            file_menu, "Export glTF (one file per c&lip)…", self.export_gltf_per_clip
        )
        self._action(file_menu, "Export &Textures…", self.export_textures)
        self._action(file_menu, "Extract &Everything…", self.extract_all)
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        view_menu = bar.addMenu("&View")
        for name, label, key, default in _VIEW_TOGGLES:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(load_bool_setting(key, default))
            action.toggled.connect(
                lambda on, name=name, key=key: self._toggle(name, key, on)
            )
            view_menu.addAction(action)
        view_menu.addSeparator()
        self._action(view_menu, "&Reset camera", self.viewport.reset_camera, "Home")
        view_menu.addSeparator()
        view_menu.addAction(self.assets_dock.toggleViewAction())
        view_menu.addAction(self.model_dock.toggleViewAction())
        view_menu.addSeparator()
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

    def _action(
        self, menu: QMenu, label: str, slot: Callable, shortcut=None
    ) -> QAction:  # noqa: ANN001
        action = QAction(label, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _toggle(self, name: str, key: str, on: bool) -> None:
        self.session.set_option(name, on)
        save_bool_setting(key, on)

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

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    # -- opening -------------------------------------------------------------

    def open_rom(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open ROM",
                load_str_setting(LAST_DIR_KEY),
                "DS ROM (*.nds);;All files (*)",
            )
        if not path:
            return
        try:
            self.session.open_rom(path)
        except Exception as exc:  # noqa: BLE001 - reported to the person
            log.exception("opening %s", path)
            QMessageBox.critical(self, "Cannot open ROM", f"{path}\n\n{exc}")
            return
        save_str_setting(LAST_ROM_KEY, str(path))
        save_str_setting(LAST_DIR_KEY, str(Path(path).parent))

    def open_file(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open file", load_str_setting(LAST_DIR_KEY), _DSE_FILTER
            )
        if not path:
            return
        try:
            self.session.open_file(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("opening %s", path)
            QMessageBox.critical(self, "Cannot open file", f"{path}\n\n{exc}")
            return
        save_str_setting(LAST_DIR_KEY, str(Path(path).parent))

    # -- exporting -------------------------------------------------------------

    def _current_clips(self, every: bool) -> list[tuple[BoundMotion, Clip]]:
        s = self.session
        if s.bound is None or s.motion is None:
            return []
        if every:
            return [(s.bound, c) for c in s.motion.clips]
        return [(s.bound, s.clip)] if s.clip is not None else []

    def export_gltf(self, every_clip: bool) -> None:
        model = self.session.model
        if model is None:
            QMessageBox.information(self, "Export glTF", "Load a model first.")
            return
        suggested = str(
            Path(load_str_setting(LAST_DIR_KEY)) / (Path(model.name).stem + ".glb")
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export glTF", suggested, "glTF binary (*.glb)"
        )
        if not path:
            return
        try:
            data = export_glb(
                model,
                self.session.visible_meshes(),
                self._current_clips(every_clip),
                palette=self.session.options.palette,
            )
            Path(path).write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            log.exception("exporting %s", path)
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._status(f"Wrote {path} ({len(data) // 1024} KB)")

    def export_gltf_per_clip(self) -> None:
        """One ``.glb`` per clip of the bound motion, into a chosen folder."""
        model = self.session.model
        if model is None:
            QMessageBox.information(self, "Export glTF", "Load a model first.")
            return
        clips = self._current_clips(True)
        if not clips:
            QMessageBox.information(
                self,
                "Export glTF",
                "Bind a motion first: the export is one file per clip.",
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Export one glTF per clip to", load_str_setting(LAST_DIR_KEY)
        )
        if not folder:
            return
        try:
            written = export_clips(
                model,
                self.session.visible_meshes(),
                clips,
                folder,
                Path(model.name).stem,
                palette=self.session.options.palette,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("exporting clips to %s", folder)
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        save_str_setting(LAST_DIR_KEY, folder)
        self._status(f"Wrote {len(written)} clip files to {folder}")

    def export_textures(self) -> None:
        model = self.session.model
        if model is None:
            QMessageBox.information(self, "Export textures", "Load a model first.")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Export textures to", load_str_setting(LAST_DIR_KEY)
        )
        if not folder:
            return
        written = 0
        for t in model.textures:
            if not t.available:
                continue
            for p in range(t.palette_count):
                rgba = t.rgba(p)
                suffix = f"_p{p}" if t.palette_count > 1 else ""
                (Path(folder) / f"{Path(t.name).stem}{suffix}.png").write_bytes(
                    encode_png(rgba.width, rgba.height, rgba.pixels)
                )
                written += 1
        self._status(f"Wrote {written} textures to {folder}")

    def extract_all(self) -> None:
        game = self.session.game
        if game is None:
            QMessageBox.information(self, "Extract", "Open a ROM first.")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Extract everything to", load_str_setting(LAST_DIR_KEY)
        )
        if not folder:
            return
        from dbkai.cli import _export_asset

        assets = [
            a for a in game.assets if a.kind in (AssetKind.MODEL, AssetKind.TEXTURES)
        ]
        progress = QProgressDialog("Extracting…", "Cancel", 0, len(assets), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        failed = 0
        for i, asset in enumerate(assets):
            progress.setValue(i)
            progress.setLabelText(asset.name)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            try:
                _export_asset(game, asset, Path(folder), with_motion=True)
            except Exception:  # noqa: BLE001 - one bad file must not stop the rest
                failed += 1
                log.exception("extracting %s", asset.path)
        progress.setValue(len(assets))
        self._status(
            f"Extracted to {folder}" + (f", {failed} failed" if failed else "")
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
