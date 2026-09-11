"""The slider, frame counter and *Play* button the Animation and Actions tabs
share. Both drive the one playback of the session, so both show it."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from dbkai.ui.session import Session


class Transport(QWidget):
    """A frame slider with its counter above a row of *Play* and the
    ``extras`` the tab adds. Moving the slider calls ``seek`` with the frame
    index; *Play* runs from the first frame, and the button follows the
    session's playing state whoever started it. ``frame_row`` is the slider
    and counter together, for a tab to hide when there is nothing to scrub."""

    def __init__(
        self,
        session: Session,
        seek: Callable[[int], None],
        extras: Sequence[QWidget] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_label = QLabel("–")
        self.play = QPushButton("Play")
        self.play.setCheckable(True)
        self.frame_row = QWidget()
        frame_row = QHBoxLayout(self.frame_row)
        frame_row.setContentsMargins(0, 0, 0, 0)
        frame_row.addWidget(self.slider, 1)
        frame_row.addWidget(self.frame_label)
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        for widget in extras:
            controls.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.frame_row)
        layout.addLayout(controls)
        self.slider.valueChanged.connect(seek)
        self.play.toggled.connect(self._play_toggled)
        session.playing_changed.connect(self._playing)
        self.show_nothing()

    def show_frame(self, index: int, count: int) -> None:
        """Show frame ``index`` of ``count`` without seeking to it."""
        with QSignalBlocker(self.slider):
            self.slider.setEnabled(True)
            self.slider.setRange(0, max(count - 1, 0))
            self.slider.setValue(index)
        self.frame_label.setText(f"{index + 1} / {count}")

    def show_nothing(self) -> None:
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, 0)
            self.slider.setEnabled(False)
        self.frame_label.setText("–")

    def _play_toggled(self, on: bool) -> None:
        if on:
            self.session.play(from_start=True)
        else:
            self.session.stop()

    def _playing(self, playing: bool) -> None:
        with QSignalBlocker(self.play):
            self.play.setChecked(playing)
        self.play.setText("Pause" if playing else "Play")
