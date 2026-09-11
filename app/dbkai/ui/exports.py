"""The File menu's export commands: ask where to write, write, and report.

Each command reads what the :class:`~dbkai.ui.session.Session` has open, asks
for a destination starting in the folder last used, and says what it wrote in
the status bar - or, when writing fails, in a message box. The writing itself
is Qt-free: :mod:`dbkai.export`, and :mod:`dbkai.extract` for what the
command line writes too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

from dbkai.export.gltf import export_clips, export_glb
from dbkai.extract import EXTRACTABLE, export_asset, texture_pngs
from dbkai.formats import dsa
from dbkai.model.action import action_take
from dbkai.model.animation import BoundMotion, Clip, Take
from dbkai.model.scene import MeshData, Model
from dbkai.ui.session import Session
from dbkai.ui.settings import load_str_setting, save_str_setting

log = logging.getLogger(__name__)

#: The folder a file was last opened from or written to, where every file
#: dialog starts.
LAST_DIR_KEY = "file/last_dir"

_GLTF_TITLE = "Export glTF"


class Exports:
    """The export commands over ``session``. ``parent`` owns the dialogs and
    ``status`` shows the line that says what was written."""

    def __init__(
        self, parent: QWidget, session: Session, status: Callable[[str], None]
    ) -> None:
        self.parent = parent
        self.session = session
        self.status = status

    # -- glTF -----------------------------------------------------------------

    def gltf(self, every_clip: bool) -> None:
        """The visible meshes with the current clip, or every clip of the
        bound motion, as one ``.glb``."""
        model = self._model(_GLTF_TITLE)
        if model is None:
            return
        self._write_glb(
            model,
            self.session.visible_meshes(),
            self._clips(every_clip),
            [],
            Path(model.name).stem,
        )

    def gltf_action(self) -> None:
        """The chosen action as one animation: its poses frame by frame and
        its part switches as node visibility, so the file carries every mesh
        not hidden by hand."""
        s = self.session
        model = self._model(_GLTF_TITLE)
        if model is None:
            return
        if s.action is None:
            QMessageBox.information(
                self.parent, _GLTF_TITLE, "Choose an action in the Actions tab first."
            )
            return
        file, action = s.action
        take = action_take(
            model.skeleton,
            file,
            action,
            s.game.motion_set if s.game is not None else lambda _set_id: None,
            dsa.join_mask(*s.rest_visibility(model)),
        )
        meshes = [m for m in model.meshes if m.uid not in s.visibility.hidden]
        stem = f"{Path(model.name).stem}__{take.name}"
        self._write_glb(model, meshes, [], [take], stem)

    def gltf_per_clip(self) -> None:
        """One ``.glb`` per clip of the bound motion, into a chosen folder."""
        model = self._model(_GLTF_TITLE)
        if model is None:
            return
        clips = self._clips(True)
        if not clips:
            QMessageBox.information(
                self.parent,
                _GLTF_TITLE,
                "Bind a motion first: the export is one file per clip.",
            )
            return
        folder = self._folder("Export one glTF per clip to")
        if folder is None:
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
        except Exception as exc:  # noqa: BLE001 - reported to the person
            self._failed(f"exporting clips to {folder}", exc)
            return
        self.status(f"Wrote {len(written)} clip files to {folder}")

    def _clips(self, every: bool) -> list[tuple[BoundMotion, Clip]]:
        """The current clip, or every clip of the bound motion."""
        s = self.session
        if s.bound is None or s.motion is None:
            return []
        if every:
            return [(s.bound, c) for c in s.motion.clips]
        return [(s.bound, s.clip)] if s.clip is not None else []

    def _write_glb(
        self,
        model: Model,
        meshes: list[MeshData],
        clips: list[tuple[BoundMotion, Clip]],
        takes: list[Take],
        stem: str,
    ) -> None:
        suggested = str(Path(load_str_setting(LAST_DIR_KEY)) / (stem + ".glb"))
        path, _ = QFileDialog.getSaveFileName(
            self.parent, _GLTF_TITLE, suggested, "glTF binary (*.glb)"
        )
        if not path:
            return
        save_str_setting(LAST_DIR_KEY, str(Path(path).parent))
        try:
            data = export_glb(
                model,
                meshes,
                clips,
                palette=self.session.options.palette,
                takes=takes,
            )
            Path(path).write_bytes(data)
        except Exception as exc:  # noqa: BLE001 - reported to the person
            self._failed(f"exporting {path}", exc)
            return
        self.status(f"Wrote {path} ({len(data) // 1024} KB)")

    # -- textures and everything ----------------------------------------------

    def textures(self) -> None:
        """Every texture of the model, every palette of it, as PNG."""
        model = self._model("Export textures")
        if model is None:
            return
        folder = self._folder("Export textures to")
        if folder is None:
            return
        written = 0
        try:
            for name, png in texture_pngs(model.textures):
                (folder / name).write_bytes(png)
                written += 1
        except Exception as exc:  # noqa: BLE001 - reported to the person
            self._failed(f"exporting textures to {folder}", exc)
            return
        self.status(f"Wrote {written} textures to {folder}")

    def extract_all(self) -> None:
        """Every model and texture set of the ROM, with its motions, the way
        the command-line extractor writes them."""
        game = self.session.game
        if game is None:
            QMessageBox.information(self.parent, "Extract", "Open a ROM first.")
            return
        folder = self._folder("Extract everything to")
        if folder is None:
            return
        assets = [a for a in game.assets if a.kind in EXTRACTABLE]
        progress = QProgressDialog("Extracting…", "Cancel", 0, len(assets), self.parent)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        done = failed = 0
        try:
            for asset in assets:
                progress.setValue(done)
                progress.setLabelText(asset.name)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break
                try:
                    export_asset(game, asset, folder, with_motion=True)
                except Exception:  # noqa: BLE001 - one bad file must not stop the rest
                    failed += 1
                    log.exception("extracting %s", asset.path)
                done += 1
            progress.setValue(len(assets))
        finally:
            # Parented to the window, so it would otherwise outlive the run.
            progress.deleteLater()
        self.status(
            f"Extracted {done} of {len(assets)} assets to {folder}"
            + (f", {failed} failed" if failed else "")
        )

    # -- shared steps ---------------------------------------------------------

    def _model(self, title: str) -> Model | None:
        """The model on screen, or ``None`` after saying there is none."""
        model = self.session.model
        if model is None:
            QMessageBox.information(self.parent, title, "Load a model first.")
        return model

    def _folder(self, title: str) -> Path | None:
        """A folder to write into, remembered for the next dialog; ``None``
        when the dialog is cancelled."""
        folder = QFileDialog.getExistingDirectory(
            self.parent, title, load_str_setting(LAST_DIR_KEY)
        )
        if not folder:
            return None
        save_str_setting(LAST_DIR_KEY, folder)
        return Path(folder)

    def _failed(self, what: str, exc: Exception) -> None:
        """Log the exception being handled and show it to the person."""
        log.exception(what)
        QMessageBox.critical(self.parent, "Export failed", str(exc))
