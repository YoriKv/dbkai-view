"""The docks beside the viewport: parts, animation, materials and skeleton.

Each panel is a widget over the :class:`~dbkai.ui.session.Session`. They
rebuild themselves when the session says the model or motion changed and
push the user's choices back through its methods.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbkai.formats import dsa
from dbkai.model.animation import Motion
from dbkai.ui.session import Session

_ROLE = Qt.ItemDataRole.UserRole


# -- parts --------------------------------------------------------------------


class PartsPanel(QWidget):
    """The two game masks (mesh groups and material parts) and a per-mesh
    override, as three checkable trees. *Rest* restores the game's rest
    preset; the Actions tab drives the masks from a chosen action or preset
    instead."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Meshes"])
        self.tree.setColumnWidth(0, 220)
        buttons = QHBoxLayout()
        self.reset = QPushButton("Rest")
        self.everything = QPushButton("Show all")
        buttons.addWidget(self.reset)
        buttons.addWidget(self.everything)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(buttons)
        layout.addWidget(self.tree)
        self.reset.clicked.connect(session.reset_visibility)
        self.everything.clicked.connect(session.show_everything)
        self.tree.itemChanged.connect(self._changed)
        session.model_changed.connect(self.rebuild)
        session.visibility_changed.connect(self.refresh)
        self._building = False

    def rebuild(self) -> None:
        self._building = True
        self.tree.clear()
        model = self.session.model
        if model is None:
            self._building = False
            return
        groups = QTreeWidgetItem(["Groups (mesh slots)", ""])
        parts = QTreeWidgetItem(["Parts (material slots)", ""])
        meshes = QTreeWidgetItem(["Meshes", str(len(model.meshes))])
        self.tree.addTopLevelItems([groups, parts, meshes])
        for g in model.groups:
            names = _distinct(m.name for m in model.meshes if m.group == g)
            item = QTreeWidgetItem(
                [f"{g}: {names}", str(sum(1 for m in model.meshes if m.group == g))]
            )
            item.setData(0, _ROLE, ("group", g))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            groups.addChild(item)
        for p in model.parts:
            names = _distinct(
                model.materials[m.material].name
                for m in model.meshes
                if m.part == p and m.material < len(model.materials)
            )
            item = QTreeWidgetItem(
                [f"{p}: {names}", str(sum(1 for m in model.meshes if m.part == p))]
            )
            item.setData(0, _ROLE, ("part", p))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parts.addChild(item)
        for m in model.meshes:
            bone = model.skeleton.names[m.bone] if m.bone < len(model.skeleton) else "?"
            item = QTreeWidgetItem([m.name, f"g{m.group} p{m.part} {bone}"])
            item.setData(0, _ROLE, ("mesh", m.uid))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setToolTip(
                0,
                f"{m.vertex_count} vertices, {m.face_count} triangles, bone {bone}"
                + (", skinned" if m.skinned else "")
                + (", double-sided" if m.double_sided else ""),
            )
            meshes.addChild(item)
        groups.setExpanded(True)
        parts.setExpanded(True)
        self._building = False
        self.refresh()

    def refresh(self) -> None:
        vis = self.session.visibility
        self._building = True
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                item = top.child(j)
                kind, value = item.data(0, _ROLE)
                if kind == "group":
                    on = value in vis.groups
                elif kind == "part":
                    on = value in vis.parts
                else:
                    on = value not in vis.hidden
                item.setCheckState(
                    0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )
        self._building = False

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building or column != 0:
            return
        data = item.data(0, _ROLE)
        if not data:
            return
        kind, value = data
        on = item.checkState(0) == Qt.CheckState.Checked
        if kind == "group":
            self.session.set_group(value, on)
        elif kind == "part":
            self.session.set_part(value, on)
        else:
            self.session.set_mesh_hidden(value, not on)


def _distinct(names) -> str:  # noqa: ANN001
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    text = ", ".join(seen[:4])
    return text + (", …" if len(seen) > 4 else "")


# -- animation ----------------------------------------------------------------


class AnimationPanel(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.source = QComboBox()
        self.browse = QPushButton("Open motion file…")
        self.clips = QListWidget()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_label = QLabel("–")
        self.play = QPushButton("Play")
        self.play.setCheckable(True)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.05, 8.0)
        self.speed.setSingleStep(0.25)
        self.speed.setValue(1.0)
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.bones_label = QLabel("")
        form = QFormLayout()
        form.addRow("Motion", self.source)
        form.addRow("", self.browse)
        form.addRow("Bones", self.bones_label)
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        controls.addWidget(QLabel("Speed"))
        controls.addWidget(self.speed)
        controls.addWidget(self.loop)
        frame_row = QHBoxLayout()
        frame_row.addWidget(self.slider, 1)
        frame_row.addWidget(self.frame_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.clips, 1)
        layout.addLayout(frame_row)
        layout.addLayout(controls)
        self.source.currentIndexChanged.connect(self._source_chosen)
        self.browse.clicked.connect(self._browse)
        self.clips.currentRowChanged.connect(self._clip_chosen)
        self.slider.valueChanged.connect(session.set_clip_frame)
        self.play.toggled.connect(self._play_toggled)
        self.speed.valueChanged.connect(self._speed_changed)
        self.loop.toggled.connect(self._loop_toggled)
        session.model_changed.connect(self.rebuild_sources)
        session.motion_changed.connect(self.rebuild_clips)
        session.frame_changed.connect(self._frame)
        session.playing_changed.connect(self._playing)

    def rebuild_sources(self) -> None:
        with QSignalBlocker(self.source):
            self.source.clear()
            self.source.addItem("Bind pose", None)
            for asset in self.session.motion_choices():
                self.source.addItem(asset.name, asset)
        self.rebuild_clips()

    def rebuild_clips(self) -> None:
        motion = self.session.motion
        with QSignalBlocker(self.source):
            index = 0
            for i in range(self.source.count()):
                asset = self.source.itemData(i)
                if (
                    motion is not None
                    and asset is not None
                    and asset.name == motion.name
                ):
                    index = i
            if motion is not None and index == 0:
                if self.source.findText(motion.name) < 0:
                    self.source.addItem(motion.name, "loaded")
                index = self.source.findText(motion.name)
            self.source.setCurrentIndex(index)
        with QSignalBlocker(self.clips):
            self.clips.clear()
            if motion is not None:
                for clip in motion.clips:
                    item = QListWidgetItem(f"{clip.name}  ({clip.frame_count})")
                    item.setData(_ROLE, clip)
                    self.clips.addItem(item)
                if self.session.clip is not None:
                    for i, clip in enumerate(motion.clips):
                        if clip is self.session.clip:
                            self.clips.setCurrentRow(i)
        bound = self.session.bound
        if bound is not None:
            self.bones_label.setText(
                f"{bound.matched} of {len(bound.skeleton)} matched"
            )
        else:
            self.bones_label.setText("")
        self._frame(self.session.frame)

    def _frame(self, frame: int) -> None:
        clip = self.session.clip
        with QSignalBlocker(self.slider):
            if clip is None:
                self.slider.setRange(0, 0)
                self.slider.setEnabled(False)
                self.frame_label.setText("–")
            else:
                self.slider.setEnabled(True)
                self.slider.setRange(0, clip.frame_count - 1)
                self.slider.setValue(frame - clip.start)
                self.frame_label.setText(
                    f"{frame - clip.start + 1} / {clip.frame_count}"
                )

    def _source_chosen(self, index: int) -> None:
        asset = self.source.itemData(index)
        if asset is None:
            self.session.set_motion(None)
        elif asset == "loaded":
            return
        elif self.session.game is not None:
            self.session.set_motion(self.session.game.load_motion(asset))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open motion", "", "DSE files (*.dse *.dsez *.dse7);;All files (*)"
        )
        if path:
            self.session.open_file(path)

    def _clip_chosen(self, row: int) -> None:
        item = self.clips.item(row)
        if item is not None:
            self.session.set_clip(item.data(_ROLE))

    def _play_toggled(self, on: bool) -> None:
        if on:
            self.session.play()
        else:
            self.session.stop()

    def _playing(self, playing: bool) -> None:
        with QSignalBlocker(self.play):
            self.play.setChecked(playing)
        self.play.setText("Pause" if playing else "Play")

    def _speed_changed(self, value: float) -> None:
        self.session.speed = value

    def _loop_toggled(self, on: bool) -> None:
        self.session.loop = on


# -- materials ----------------------------------------------------------------


class MaterialsPanel(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.palette = QSpinBox()
        self.palette.setRange(0, 0)
        self.textures = QListWidget()
        self.textures.setIconSize(QPixmap(96, 96).size())
        self.materials = QTreeWidget()
        self.materials.setHeaderLabels(["Material", "Texture", "Part", "Wrap"])
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
        session.model_changed.connect(self.rebuild)

    def rebuild(self) -> None:
        model = self.session.model
        self.textures.clear()
        self.materials.clear()
        if model is None:
            self.palette.setRange(0, 0)
            return
        count = max([t.palette_count for t in model.textures] or [1])
        with QSignalBlocker(self.palette):
            self.palette.setRange(0, count - 1)
            self.palette.setValue(min(self.session.options.palette, count - 1))
        palette = self.palette.value()
        for t in model.textures:
            label = f"{t.name}\n{t.width}×{t.height} {t.format.name.lower()}"
            if t.palette_count > 1:
                label += f", {t.palette_count} palettes"
            if not t.available:
                label += " (no data)"
            item = QListWidgetItem(label)
            if t.available:
                try:
                    rgba = t.rgba(min(palette, t.palette_count - 1))
                    image = QImage(
                        rgba.pixels,
                        rgba.width,
                        rgba.height,
                        rgba.width * 4,
                        QImage.Format.Format_RGBA8888,
                    ).copy()
                    item.setIcon(QPixmap.fromImage(image))
                except Exception:  # noqa: BLE001 - shown without a preview
                    pass
            self.textures.addItem(item)
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

    def _palette_changed(self, value: int) -> None:
        self.session.set_option("palette", value)
        self.rebuild()


# -- skeleton -----------------------------------------------------------------


class SkeletonPanel(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bone", "Rest position"])
        self.tree.setColumnWidth(0, 200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        session.model_changed.connect(self.rebuild)

    def rebuild(self) -> None:
        self.tree.clear()
        model = self.session.model
        if model is None:
            return
        sk = model.skeleton
        items: dict[int, QTreeWidgetItem] = {}
        for i in sk.order:
            t = sk.bind_world[i][:3, 3]
            item = QTreeWidgetItem([sk.names[i], f"{t[0]:.2f}, {t[1]:.2f}, {t[2]:.2f}"])
            p = sk.parents[i]
            if p in items:
                items[p].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            items[i] = item
        self.tree.expandAll()


def motion_label(motion: Motion | None) -> str:
    return motion.name if motion else "bind pose"


# -- actions ------------------------------------------------------------------


class ActionsPanel(QWidget):
    """The character's actions from its ``.dsa`` files, and the game's
    visibility presets. Choosing an action plays it: the clip, the frame and
    the part masks all come from its commands."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Action", "Frames", "Plays"])
        self.tree.setColumnWidth(0, 150)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.clear = QPushButton("No action")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_label = QLabel("–")
        self.play = QPushButton("Play")
        self.play.setCheckable(True)
        row = QHBoxLayout()
        row.addWidget(self.clear)
        row.addWidget(self.play)
        frame_row = QHBoxLayout()
        frame_row.addWidget(self.slider, 1)
        frame_row.addWidget(self.frame_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree, 1)
        layout.addLayout(frame_row)
        layout.addLayout(row)
        layout.addWidget(self.info)
        self.tree.currentItemChanged.connect(self._chosen)
        self.clear.clicked.connect(lambda: session.set_action(None))
        self.slider.valueChanged.connect(session.set_action_frame)
        self.play.toggled.connect(self._play_toggled)
        session.actions_changed.connect(self.rebuild)
        session.frame_changed.connect(self._frame)
        session.visibility_changed.connect(self._frame)
        session.playing_changed.connect(self._playing)
        self._building = False

    def rebuild(self) -> None:
        self._building = True
        self.tree.clear()
        presets = self.session.game.visibility_presets if self.session.game else {}
        if presets:
            top = QTreeWidgetItem(["Presets (parameter table)", str(len(presets)), ""])
            self.tree.addTopLevelItem(top)
            for state, mask in sorted(presets.items()):
                groups, parts = dsa.split_mask(mask)
                item = QTreeWidgetItem(
                    [
                        str(state),
                        "",
                        f"{mask:#010x} groups {sorted(groups)} parts {sorted(parts)}",
                    ]
                )
                item.setData(0, _ROLE, ("preset", mask))
                top.addChild(item)
        for file in self.session.action_files:
            top = QTreeWidgetItem([file.name, str(len(file.actions)), ""])
            self.tree.addTopLevelItem(top)
            for action in file.actions:
                item = QTreeWidgetItem(
                    [str(action.action_id), str(action.duration), _plays(file, action)]
                )
                item.setData(0, _ROLE, ("action", file, action))
                top.addChild(item)
                if self.session.action is not None and self.session.action[1] is action:
                    self.tree.setCurrentItem(item)
            top.setExpanded(True)
        self._building = False
        self._frame()

    def _chosen(
        self, item: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if self._building or item is None:
            return
        choice = item.data(0, _ROLE)
        if not choice:
            return
        if choice[0] == "preset":
            self.session.apply_mask(choice[1])
        else:
            self.session.set_action((choice[1], choice[2]))

    def _frame(self, *_args: object) -> None:
        action = self.session.action
        with QSignalBlocker(self.slider):
            if action is None:
                self.slider.setRange(0, 0)
                self.slider.setEnabled(False)
                self.frame_label.setText("–")
                self.info.setText(
                    "Parts as set in the Parts tab; pose from the Animation tab."
                )
                return
            duration = max(action[1].duration, 1)
            self.slider.setEnabled(True)
            self.slider.setRange(0, duration - 1)
            self.slider.setValue(self.session.action_frame)
            self.frame_label.setText(f"{self.session.action_frame + 1} / {duration}")
        mask = self.session.action_mask()
        if mask is None:
            mask_text = "no visibility command at this frame"
        else:
            groups, parts = dsa.split_mask(mask)
            mask_text = (
                f"mask {mask:#010x}: groups {sorted(groups)}, parts {sorted(parts)}"
            )
        self.info.setText(f"{self.session.action_clip_name()}; {mask_text}")

    def _play_toggled(self, on: bool) -> None:
        if on:
            self.session.play()
        else:
            self.session.stop()

    def _playing(self, playing: bool) -> None:
        with QSignalBlocker(self.play):
            self.play.setChecked(playing)
        self.play.setText("Pause" if playing else "Play")


def _plays(file: dsa.DsaFile, action: dsa.Action) -> str:
    """A short description of the clips an action plays."""
    numbers: list[str] = []
    for c in action.motions:
        for seg in c.segments:
            if 0 <= seg.resource < len(file.resources):
                res = file.resources[seg.resource]
                text = (
                    f"{res.number:05d}"
                    if not res.embedded
                    else f"embedded {res.number}"
                )
                if text not in numbers:
                    numbers.append(text)
    return ", ".join(numbers)
