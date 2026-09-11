"""The Actions tab: the character's action sets and the game's visibility
presets, and the transport of the chosen action."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dbkai.formats import dsa
from dbkai.ui.elided import show_elided_text
from dbkai.ui.panels.transport import Transport
from dbkai.ui.session import Session

_ROLE = Qt.ItemDataRole.UserRole

#: Source entry that lists the parameter table's visibility presets.
_PRESETS = "presets"

#: The list's columns for an action set, and for the presets: a preset has
#: no frames, so that column goes with the slider.
_ACTION_COLUMNS = ["Action", "Frames", "Plays"]
_PRESET_COLUMNS = ["Preset", "", "Shows"]
_FRAMES = 1


class ActionsPanel(QWidget):
    """The character's actions, laid out like the Animation tab: a source
    (one of the model's ``.dsa`` files, or the game's visibility presets), a
    list of what it holds, and the transport. Choosing an action plays it:
    the clip, the frame and the part masks all come from its commands, and
    switching actions keeps the transport running. Choosing a preset shows
    its mask the same way, and stays chosen until the parts are set some
    other way; having no frames, it has no slider."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._building = False
        #: What the source list was built from: the action set objects, then
        #: the game whose presets it offers (or ``None``).
        self._sources: list[object] = []
        self.source = QComboBox()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(_ACTION_COLUMNS)
        self.tree.setColumnWidth(0, 110)
        self.tree.setRootIsDecorated(False)
        show_elided_text(self.tree)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.clear = QPushButton("No action")
        self.remove = QPushButton("Remove set")
        self.remove.setToolTip(
            "Take an action set added from the Assets dock out again"
        )
        self.transport = Transport(
            session, session.set_action_frame, [self.clear, self.remove]
        )
        self.slider = self.transport.slider
        self.frame_label = self.transport.frame_label
        self.play = self.transport.play
        form = QFormLayout()
        form.addRow("Action set", self.source)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.transport)
        layout.addWidget(self.info)
        self.source.currentIndexChanged.connect(self._source_chosen)
        self.tree.currentItemChanged.connect(self._chosen)
        self.clear.clicked.connect(self._clear_action)
        self.remove.clicked.connect(self._remove_source)
        session.actions_changed.connect(self.rebuild)
        session.game_changed.connect(self.rebuild)  # the presets come with the ROM
        session.frame_changed.connect(self._frame)
        session.visibility_changed.connect(self._frame)
        self._frame()

    # -- building -------------------------------------------------------------

    def rebuild(self) -> None:
        """Refill the source list when the files changed; otherwise only
        follow the session's current action or preset."""
        files = list(self.session.action_sets)
        game = self.session.game
        presets = game is not None and bool(game.visibility_presets)
        names = [f.name for f in files] + ([_PRESETS] if presets else [])
        # By identity: a file added again under the same name is new data.
        sources: list[object] = [*files, game if presets else None]
        changed = len(sources) != len(self._sources) or any(
            a is not b for a, b in zip(sources, self._sources, strict=True)
        )
        wanted = self._wanted_source(names)
        if changed:
            self._sources = sources
            with QSignalBlocker(self.source):
                self.source.clear()
                for n in names:
                    self.source.addItem(n if n != _PRESETS else "Presets", n)
        if changed or self.source.currentData() != wanted:
            if wanted is not None:
                with QSignalBlocker(self.source):
                    self.source.setCurrentIndex(names.index(wanted))
            self._fill(wanted)
        self._select_current()
        self._update_remove()
        self._frame()

    def _wanted_source(self, names: list[str]) -> str | None:
        """The source to show: the file of the running action or the presets
        of the chosen one, else what is shown now if it still exists, else
        the first."""
        if self.session.action is not None and self.session.action[0].name in names:
            return self.session.action[0].name
        if self.session.preset is not None and _PRESETS in names:
            return _PRESETS
        current = self.source.currentData()
        if current in names:
            return current
        return names[0] if names else None

    def _fill(self, name: str | None) -> None:
        self._building = True
        try:
            self.tree.clear()
            presets = name == _PRESETS
            self.tree.setHeaderLabels(_PRESET_COLUMNS if presets else _ACTION_COLUMNS)
            self.tree.setColumnHidden(_FRAMES, presets)
            if presets and self.session.game is not None:
                table = self.session.game.visibility_presets
                for state, mask in sorted(table.items()):
                    item = QTreeWidgetItem([str(state), "", _shows(mask)])
                    item.setData(0, _ROLE, ("preset", state))
                    self.tree.addTopLevelItem(item)
                return
            for file in self.session.action_sets:
                if file.name != name:
                    continue
                for action in file.actions:
                    item = QTreeWidgetItem(
                        [
                            str(action.action_id),
                            str(action.duration),
                            _plays(file, action),
                        ]
                    )
                    item.setData(0, _ROLE, ("action", file, action))
                    self.tree.addTopLevelItem(item)
        finally:
            self._building = False

    def _select_current(self) -> None:
        """Highlight the session's choice, an action or a preset, if the
        list holds it."""
        action = self.session.action
        preset = self.session.preset
        self._building = True
        try:
            if action is None and preset is None:
                self.tree.setCurrentItem(None)
                return
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                data = item.data(0, _ROLE)
                if not data:
                    continue
                if data[0] == "action":
                    found = action is not None and data[2] is action[1]
                else:
                    found = data[1] == preset
                if found:
                    if self.tree.currentItem() is not item:
                        self.tree.setCurrentItem(item)
                    return
        finally:
            self._building = False

    # -- choosing -------------------------------------------------------------

    def _source_chosen(self, _index: int) -> None:
        self._fill(self.source.currentData())
        self._select_current()
        self._update_remove()
        self._frame()

    def _update_remove(self) -> None:
        name = self.source.currentData()
        self.remove.setEnabled(
            isinstance(name, str)
            and name != _PRESETS
            and self.session.is_added_action_set(name)
        )

    def _remove_source(self) -> None:
        name = self.source.currentData()
        if isinstance(name, str) and name != _PRESETS:
            self.session.remove_action_set(name)

    def _clear_action(self) -> None:
        if self.session.preset is not None:
            self.session.set_preset(None)
        else:
            self.session.set_action(None)

    def _chosen(
        self, item: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if self._building or item is None:
            return
        choice = item.data(0, _ROLE)
        if not choice:
            return
        if choice[0] == "preset":
            self.session.set_preset(choice[1])
        else:
            self.session.set_action((choice[1], choice[2]))

    # -- transport ------------------------------------------------------------

    def _frame(self, *_args: object) -> None:
        action = self.session.action
        preset = self.session.preset
        # The slider scrubs an action; with the presets listed and none
        # running there is nothing it could do, so it goes.
        self.transport.frame_row.setVisible(
            action is not None or self.source.currentData() != _PRESETS
        )
        if action is None:
            self.transport.show_nothing()
            if preset is not None and self.session.game is not None:
                mask = self.session.game.visibility_presets[preset]
                self.info.setText(f"preset {preset}: {_mask_text(mask)}")
            else:
                self.info.setText(
                    "Parts as set in the Parts tab; pose from the Animation tab."
                )
            return
        self.transport.show_frame(self.session.action_frame, max(action[1].duration, 1))
        mask = self.session.action_mask()
        if mask is None:
            mask_text = "no visibility command at this frame"
        else:
            mask_text = _mask_text(mask)
        self.info.setText(f"{self.session.action_clip_name()}; {mask_text}")


def _shows(mask: int) -> str:
    groups, parts = dsa.split_mask(mask)
    return f"{mask:#010x} groups {sorted(groups)} parts {sorted(parts)}"


def _mask_text(mask: int) -> str:
    groups, parts = dsa.split_mask(mask)
    return f"mask {mask:#010x}: groups {sorted(groups)}, parts {sorted(parts)}"


def _plays(file: dsa.ActionSet, action: dsa.Action) -> str:
    """The resources an action's motion commands play, distinct and in
    order: a clip number of the body's motion set, or an embedded one."""
    numbers: list[str] = []
    for command in action.motions:
        for seg in command.segments:
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
