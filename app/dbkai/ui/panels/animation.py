"""The Animation tab: which motion is bound, its clips, and the transport."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dbkai.game import Asset
from dbkai.model.animation import Motion
from dbkai.ui.panels.transport import Transport
from dbkai.ui.session import Session

_ROLE = Qt.ItemDataRole.UserRole


class AnimationPanel(QWidget):
    """A *Motion* chooser (the bind pose, the model's motion files, and any
    other motion bound to it), the clips of the bound motion, and the
    transport with speed and loop."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        #: The motion whose clips the list shows.
        self._listed: Motion | None = None
        self.source = QComboBox()
        self.browse = QPushButton("Open motion file…")
        self.clips = QListWidget()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.05, 8.0)
        self.speed.setSingleStep(0.25)
        self.speed.setValue(session.speed)
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(session.loop)
        self.transport = Transport(
            session,
            session.set_clip_frame,
            [QLabel("Speed"), self.speed, self.loop],
        )
        self.slider = self.transport.slider
        self.frame_label = self.transport.frame_label
        self.play = self.transport.play
        self.bones_label = QLabel("")
        form = QFormLayout()
        form.addRow("Motion", self.source)
        form.addRow("", self.browse)
        form.addRow("Bones", self.bones_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.clips, 1)
        layout.addWidget(self.transport)
        self.source.currentIndexChanged.connect(self._source_chosen)
        self.browse.clicked.connect(self._browse)
        self.clips.currentRowChanged.connect(self._clip_chosen)
        self.speed.valueChanged.connect(self._speed_changed)
        self.loop.toggled.connect(self._loop_toggled)
        session.model_changed.connect(self.rebuild_sources)
        session.motion_changed.connect(self.rebuild_clips)
        session.frame_changed.connect(self._frame)

    def rebuild_sources(self) -> None:
        with QSignalBlocker(self.source):
            self.source.clear()
            self.source.addItem("Bind pose", None)
            for asset in self.session.motion_choices():
                self.source.addItem(asset.name, asset)
        self.rebuild_clips()

    def rebuild_clips(self) -> None:
        """Follow the session's motion and clip; the list is refilled only
        when the motion itself changed."""
        motion = self.session.motion
        self._show_source(motion)
        with QSignalBlocker(self.clips):
            if motion is not self._listed:
                self._listed = motion
                self.clips.clear()
                for clip in motion.clips if motion is not None else []:
                    item = QListWidgetItem(f"{clip.name}  ({clip.frame_count})")
                    item.setData(_ROLE, clip)
                    self.clips.addItem(item)
            row = -1
            if motion is not None and self.session.clip is not None:
                row = next(
                    (
                        i
                        for i, clip in enumerate(motion.clips)
                        if clip is self.session.clip
                    ),
                    -1,
                )
            self.clips.setCurrentRow(row)
        bound = self.session.bound
        self.bones_label.setText(
            f"{bound.matched} of {len(bound.skeleton)} matched" if bound else ""
        )
        self._frame(self.session.frame)

    def _show_source(self, motion: Motion | None) -> None:
        """Select the motion's entry. Entry 0 is the bind pose; a motion that
        is not one of the model's choices (opened from disk, or bound by an
        action) gets an entry of its own holding the motion, so choosing it
        again binds it again."""
        with QSignalBlocker(self.source):
            index = 0
            if motion is not None:
                index = self.source.findText(motion.name)
                if index < 0:
                    self.source.addItem(motion.name, motion)
                    index = self.source.count() - 1
                elif isinstance(self.source.itemData(index), Motion):
                    self.source.setItemData(index, motion)
            self.source.setCurrentIndex(index)

    def _frame(self, frame: int) -> None:
        clip = self.session.clip
        if clip is None:
            self.transport.show_nothing()
        else:
            self.transport.show_frame(frame - clip.start, clip.frame_count)

    def _source_chosen(self, index: int) -> None:
        choice = self.source.itemData(index)
        if choice is None:
            self.session.set_motion(None)
        elif isinstance(choice, Motion):
            self.session.set_motion(choice)
        elif isinstance(choice, Asset) and self.session.game is not None:
            try:
                motion = self.session.game.load_motion(choice)
            except Exception as exc:  # noqa: BLE001 - reported to the person
                QMessageBox.critical(
                    self, "Cannot load motion", f"{choice.path}\n\n{exc}"
                )
                self._show_source(self.session.motion)
                return
            self.session.set_motion(motion)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open motion", "", "DSE files (*.dse *.dsez *.dse7);;All files (*)"
        )
        if not path:
            return
        try:
            self.session.open_file(path)
        except Exception as exc:  # noqa: BLE001 - reported to the person
            QMessageBox.critical(self, "Cannot open file", f"{path}\n\n{exc}")

    def _clip_chosen(self, row: int) -> None:
        item = self.clips.item(row)
        if item is not None:
            self.session.set_clip(item.data(_ROLE))

    def _speed_changed(self, value: float) -> None:
        self.session.speed = value

    def _loop_toggled(self, on: bool) -> None:
        self.session.loop = on
