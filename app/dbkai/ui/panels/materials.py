"""The Materials tab: textures with their preview, the palette, and the
material table."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbkai.model.scene import TextureData
from dbkai.ui.elided import show_elided_text
from dbkai.ui.session import Session


class MaterialsPanel(QWidget):
    """The model's textures, previewed in the chosen palette, and its
    materials. The palette spinner sets the session's palette and follows
    it when an action's colour scheme changes it."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.palette = QSpinBox()
        self.palette.setRange(0, 0)
        self.textures = QListWidget()
        self.textures.setIconSize(QSize(96, 96))
        self.materials = QTreeWidget()
        self.materials.setHeaderLabels(["Material", "Texture", "Part", "Wrap"])
        show_elided_text(self.materials)
        form = QFormLayout()
        form.addRow("Palette", self.palette)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(QLabel("Textures"))
        layout.addWidget(self.textures, 2)
        layout.addWidget(QLabel("Materials"))
        layout.addWidget(self.materials, 1)
        self.palette.valueChanged.connect(self._palette_changed)
        session.options_changed.connect(self._follow_palette)
        session.model_changed.connect(self.rebuild)

    def rebuild(self) -> None:
        model = self.session.model
        count = max([t.palette_count for t in model.textures] or [1]) if model else 1
        with QSignalBlocker(self.palette):
            self.palette.setRange(0, max(count - 1, 0))
            self.palette.setValue(min(self.session.options.palette, count - 1))
        self._fill_textures()
        self.materials.clear()
        if model is None:
            return
        for m in model.materials:
            tex = (
                model.textures[m.texture].name
                if m.texture is not None and m.texture < len(model.textures)
                else "–"
            )
            wrap = "".join(
                c
                for c, on in (
                    ("S", m.repeat_s),
                    ("T", m.repeat_t),
                    ("s", m.flip_s),
                    ("t", m.flip_t),
                )
                if on
            )
            self.materials.addTopLevelItem(
                QTreeWidgetItem([m.name, tex, str(m.part), wrap])
            )

    def _fill_textures(self) -> None:
        """List the textures, previewed in the spinner's palette."""
        self.textures.clear()
        model = self.session.model
        if model is None:
            return
        palette = self.palette.value()
        for t in model.textures:
            label = f"{t.name}\n{t.width}×{t.height} {t.format.name.lower()}"
            if t.palette_count > 1:
                label += f", {t.palette_count} palettes"
            if not t.available:
                label += " (no data)"
            item = QListWidgetItem(label)
            preview = _preview(t, palette)
            if preview is not None:
                item.setIcon(preview)
            self.textures.addItem(item)

    def _palette_changed(self, value: int) -> None:
        self.session.set_option("palette", value)
        self._fill_textures()

    def _follow_palette(self) -> None:
        """Show the palette an action's colour scheme chose."""
        value = min(self.session.options.palette, self.palette.maximum())
        if value != self.palette.value():
            with QSignalBlocker(self.palette):
                self.palette.setValue(value)
            self._fill_textures()


def _preview(texture: TextureData, palette: int) -> QPixmap | None:
    """The texture in ``palette`` (or its last one), or ``None`` when it
    cannot be decoded."""
    if not texture.available:
        return None
    try:
        rgba = texture.rgba(min(palette, texture.palette_count - 1))
    except Exception:  # noqa: BLE001 - shown without a preview
        return None
    image = QImage(
        rgba.pixels,
        rgba.width,
        rgba.height,
        rgba.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()  # the QImage only borrows ``rgba.pixels``; own the pixels
    return QPixmap.fromImage(image)
