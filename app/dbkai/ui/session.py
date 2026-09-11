"""What the viewer has open, as one object every panel watches.

The :class:`Session` owns the game data, the model on screen, the motion
bound to it and the view state (which parts are shown, which frame is
posed), and announces changes through Qt signals. Widgets read from it and
call its methods; they never talk to each other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from dbkai.formats import dsa, dse
from dbkai.game import Asset, AssetKind, GameData, unwrap
from dbkai.model import scene
from dbkai.model.action import ActionPose, action_pose
from dbkai.model.animation import BoundMotion, Clip, Motion
from dbkai.model.scene import MeshData, Model

log = logging.getLogger(__name__)

#: Playback speed of the game's motions.
FRAMES_PER_SECOND = 60


@dataclass
class ViewOptions:
    textures: bool = True
    vertex_colors: bool = True
    wireframe: bool = False
    grid: bool = True
    bones: bool = False
    culling: bool = True
    palette: int = 0


@dataclass
class Visibility:
    """The game's two masks plus the viewer's own per-mesh override."""

    groups: set[int] = field(default_factory=set)
    parts: set[int] = field(default_factory=set)
    hidden: set[int] = field(default_factory=set)

    def shows(self, mesh: MeshData) -> bool:
        return (
            mesh.group in self.groups
            and mesh.part in self.parts
            and mesh.uid not in self.hidden
        )


class Session(QObject):
    game_changed = Signal()
    model_changed = Signal()
    motion_changed = Signal()
    frame_changed = Signal(int)
    visibility_changed = Signal()
    options_changed = Signal()
    actions_changed = Signal()
    playing_changed = Signal(bool)
    status = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.game: GameData | None = None
        self.game_path: Path | None = None
        self.asset: Asset | None = None
        self.model: Model | None = None
        self.model_path: Path | None = None
        self.motion: Motion | None = None
        self.motion_name: str = ""
        self.bound: BoundMotion | None = None
        self.clip: Clip | None = None
        self.frame: int = 0
        self.visibility = Visibility()
        self.options = ViewOptions()
        #: The action sets that apply to the model, and the chosen action.
        self.action_sets: list[dsa.ActionSet] = []
        #: Names of the sets found for the model, as opposed to added by hand.
        self.own_action_sets: set[str] = set()
        self.action: tuple[dsa.ActionSet, dsa.Action] | None = None
        self.action_frame: int = 0
        #: The state id of the chosen visibility preset, the Actions tab's
        #: other kind of choice; ``None`` when none is, or an action is.
        self.preset: int | None = None
        #: The palette to go back to when the chosen action is left.
        self._palette_before_action = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // FRAMES_PER_SECOND)
        self._timer.timeout.connect(self._tick)
        self.speed = 1.0
        self._accumulator = 0.0
        self.loop = True

    # -- opening things -------------------------------------------------------

    def open_rom(self, path: str | Path) -> None:
        """Open the ROM at ``path``. A model loaded from the previous ROM goes
        with it; one opened from a file stays."""
        path = Path(path)
        game = GameData.open(path)
        self.game = game
        self.game_path = path
        if self.asset is not None:
            self._set_model(None, None, None)
        self.game_changed.emit()
        self.status.emit(f"{path.name}: {len(self.game.assets)} assets")

    def open_file(self, path: str | Path) -> None:
        """A model or motion file from disk, outside any ROM."""
        path = Path(path)
        data = unwrap(path.read_bytes())
        if not dse.is_dse(data):
            raise ValueError(f"{path.name} is not a DSE file")
        file = dse.parse(data)
        if file.meshes:
            self._set_model(scene.build(file, path.name), None, path)
        elif file.frame_count:
            self.set_motion(Motion(file, path.name))
        else:
            raise ValueError(f"{path.name} holds no meshes and no motion")

    def load_asset(self, asset: Asset) -> None:
        if self.game is None:
            raise RuntimeError("no ROM open")
        if asset.kind in (AssetKind.MOTION, AssetKind.MOTION_SET):
            self.set_motion(self.game.load_motion(asset))
            return
        if asset.kind is AssetKind.ACTION_SET:
            self.add_action_set(self.game.load_action_set(asset))
            return
        file = self.game.load_dse(asset)
        if not file.meshes and file.frame_count:
            self.set_motion(Motion(file, asset.name))
            return
        self._set_model(self.game.load_model(asset), asset, None)

    def _set_model(
        self, model: Model | None, asset: Asset | None, path: Path | None
    ) -> None:
        """Show ``model`` with everything reset to its defaults; ``None``
        empties the view."""
        self.stop()
        self.model = model
        self.asset = asset
        self.model_path = path
        self.visibility = (
            Visibility(*self.rest_visibility(model), set())
            if model is not None
            else Visibility()
        )
        self.motion = None
        self.motion_name = ""
        self.bound = None
        self.clip = None
        self.frame = 0
        self.action_sets = []
        self.own_action_sets = set()
        self.action = None
        self.action_frame = 0
        self.preset = None
        self._palette_before_action = 0
        self.set_option("palette", 0)  # an action's colour scheme does not carry over
        if self.game is not None and asset is not None:
            for a in self.game.action_sets_for(asset):
                try:
                    self.action_sets.append(self.game.load_action_set(a))
                    self.own_action_sets.add(self.action_sets[-1].name)
                except Exception:  # noqa: BLE001 - a bad action set must not hide the model
                    log.exception("loading %s", a.path)
        self.model_changed.emit()
        self.motion_changed.emit()
        self.visibility_changed.emit()
        self.actions_changed.emit()
        if model is None:
            return
        self.status.emit(
            f"{model.name}: {len(model.meshes)} meshes, "
            f"{sum(m.vertex_count for m in model.meshes)} vertices, "
            f"{len(model.skeleton)} bones, {len(model.textures)} textures"
        )
        if model.source.frame_count > 1:
            # A prop that carries its own animation frames.
            self.set_motion(Motion(model.source, model.name))
        elif self.game is not None and asset is not None:
            default = self.game.motion_set_for(asset)
            if default is not None:
                try:
                    self.set_motion(self.game.load_motion(default))
                except Exception:  # noqa: BLE001 - a bad motion must not hide the model
                    log.exception("loading %s", default.path)

    def motion_choices(self) -> list[Asset]:
        if self.game is None or self.asset is None:
            return []
        return self.game.motions_for(self.asset)

    # -- motion ---------------------------------------------------------------

    def set_motion(self, motion: Motion | None) -> None:
        """Bind ``motion`` to the model at its first clip; ``None`` shows the
        bind pose. Drops the chosen action."""
        self.stop()
        self._drop_action()
        self.motion = motion
        self.motion_name = motion.name if motion else ""
        self.bound = None
        self.clip = None
        self.frame = 0
        if motion is not None and self.model is not None:
            self.bound = BoundMotion.bind(self.model.skeleton, motion)
            if motion.clips:
                self.clip = motion.clips[0]
                self.frame = self.clip.start
        self.motion_changed.emit()
        self.frame_changed.emit(self.frame)

    def set_clip(self, clip: Clip | None) -> None:
        """Show ``clip`` from its first frame. Drops the chosen action."""
        self._drop_action()
        self.clip = clip
        self.frame = clip.start if clip else 0
        self.motion_changed.emit()
        self.frame_changed.emit(self.frame)

    def set_frame(self, frame: int) -> None:
        if self.clip is not None:
            frame = max(self.clip.start, min(frame, self.clip.end - 1))
        elif self.motion is not None:
            frame = max(0, min(frame, self.motion.frame_count - 1))
        else:
            frame = 0
        if frame != self.frame:
            self.frame = frame
            self.frame_changed.emit(frame)

    @property
    def clip_frame(self) -> int:
        return self.frame - (self.clip.start if self.clip else 0)

    def set_clip_frame(self, index: int) -> None:
        """Scrub the clip to its ``index``-th frame. Drops the chosen action,
        which would otherwise pose over the scrubbed frame."""
        self._drop_action()
        self.set_frame(index + (self.clip.start if self.clip else 0))

    def play(self, from_start: bool = False) -> None:
        """Run the clip, or the action when one is chosen; ``from_start``
        rewinds it to its first frame first."""
        if (self.clip is None and self.action is None) or self._timer.isActive():
            return
        if from_start:
            if self.action is not None:
                self.set_action_frame(0)
            elif self.clip is not None:
                self.set_frame(self.clip.start)
        self._accumulator = 0.0
        self._timer.start()
        self.playing_changed.emit(True)

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.playing_changed.emit(False)

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    def toggle_play(self) -> None:
        """Pause a running playback, or resume a paused one where it stands.
        A playback that ran to its last frame and stopped there starts over,
        since resuming it would only stop it again."""
        if self.playing:
            self.stop()
        else:
            self.play(from_start=self._at_last_frame())

    def _at_last_frame(self) -> bool:
        if self.action is not None:
            return self.action_frame >= self.action[1].duration - 1
        return self.clip is not None and self.frame >= self.clip.end - 1

    def _tick(self) -> None:
        if self.action is not None:
            self._tick_action()
            return
        if self.clip is None:
            self.stop()
            return
        self._accumulator += self.speed
        step = int(self._accumulator)
        if step <= 0:
            return
        self._accumulator -= step
        nxt = self.frame + step
        if nxt >= self.clip.end:
            if not self.loop:
                self.set_frame(self.clip.end - 1)
                self.stop()
                return
            nxt = self.clip.start + (nxt - self.clip.start) % self.clip.frame_count
        self.set_frame(nxt)

    # -- pose -----------------------------------------------------------------

    def world_matrices(self) -> np.ndarray | None:
        """Every bone's world matrix for the current frame, or the bind pose
        when nothing is bound."""
        if self.model is None:
            return None
        if self.bound is None or self.motion is None:
            return self.model.skeleton.bind_world
        return self.bound.world_matrices(self.frame)

    def skin_matrices(self) -> np.ndarray | None:
        world = self.world_matrices()
        if world is None or self.model is None:
            return None
        return self.model.skeleton.skin_matrices(world)

    # -- actions --------------------------------------------------------------

    def add_action_set(self, file: dsa.ActionSet) -> None:
        """Offer an action set's actions for the loaded model, replacing one
        of the same name. Its motions resolve against the ROM's motion sets,
        so any character can be posed with any file's actions."""
        self.action_sets = [f for f in self.action_sets if f.name != file.name]
        self.action_sets.append(file)
        if self.action is not None and self.action[0].name == file.name:
            self.set_action(None)
        self.actions_changed.emit()
        if self.model is None:
            self.status.emit(f"{file.name}: {len(file.actions)} actions; open a model")
        else:
            self.status.emit(f"{file.name}: {len(file.actions)} actions")

    def is_added_action_set(self, name: str) -> bool:
        """Whether the file was added by hand rather than found for the
        model, so it can be removed again."""
        return name not in self.own_action_sets

    def remove_action_set(self, name: str) -> None:
        """Take an added action set out again; the model's own files stay."""
        if not self.is_added_action_set(name):
            return
        if self.action is not None and self.action[0].name == name:
            self.set_action(None)
        self.action_sets = [f for f in self.action_sets if f.name != name]
        self.actions_changed.emit()

    def set_action(self, choice: tuple[dsa.ActionSet, dsa.Action] | None) -> None:
        """Play an action: its motion segments drive the clip and frame, its
        visibility and colour commands the masks, frame by frame of the
        action. ``None`` goes back to free clip scrubbing."""
        keep_playing = self.playing and choice is not None
        self.stop()
        if self.action is not None:
            self._undo_scheme()
        elif choice is not None:
            self._palette_before_action = self.options.palette
        self.action = choice
        self.action_frame = 0
        self.preset = None
        self.actions_changed.emit()
        if choice is not None:
            self.set_action_frame(0)
        if keep_playing:
            self.play()

    def set_preset(self, state: int | None) -> None:
        """Choose a visibility preset of the game's table by its state id,
        the way an action is chosen: it shows what the preset enables and
        stays the chosen one until the parts are set some other way. ``None``
        forgets it, showing what it left."""
        if state is None:
            self._leave_preset()
            return
        if self.model is None or self.game is None:
            return
        mask = self.game.visibility_presets[state]
        if self.action is not None:  # leave it, as set_action(None) would
            self.stop()
            self._undo_scheme()
            self.action = None
            self.action_frame = 0
        self.preset = state
        self._show_mask(mask)
        self.actions_changed.emit()

    def _leave_preset(self) -> None:
        """Forget the chosen preset, when the parts are set some other way."""
        if self.preset is None:
            return
        self.preset = None
        self.actions_changed.emit()

    def _drop_action(self) -> None:
        """Forget the chosen action, when the pose is chosen some other way."""
        if self.action is None:
            return
        self._undo_scheme()
        self.action = None
        self.action_frame = 0
        self.actions_changed.emit()

    def _undo_scheme(self) -> None:
        """Leaving an action undoes the colour scheme it chose."""
        self.set_option("palette", self._palette_before_action)

    def set_action_frame(self, frame: int) -> None:
        if self.action is None:
            return
        _file, action = self.action
        self.action_frame = max(0, min(frame, max(action.duration - 1, 0)))
        self._apply_action()

    def _tick_action(self) -> None:
        if self.action is None:
            return
        self._accumulator += self.speed
        step = int(self._accumulator)
        if step <= 0:
            return
        self._accumulator -= step
        duration = max(self.action[1].duration, 1)
        nxt = self.action_frame + step
        if nxt >= duration:
            if not self.loop:
                self.set_action_frame(duration - 1)
                self.stop()
                return
            nxt %= duration
        self.set_action_frame(nxt)

    def _apply_action(self) -> None:
        if self.action is None or self.model is None:
            return
        file, action = self.action
        frame = self.action_frame
        if self.game is not None:
            pose = action_pose(file, action, frame, self.game.motion_set)
            if pose is not None:
                self._show_pose(pose)
        mask = action.mask_at(frame)
        if mask is not None:
            groups, parts = self.model.visibility_from_mask(mask)
            if (groups, parts) != (self.visibility.groups, self.visibility.parts):
                self.visibility.groups, self.visibility.parts = groups, parts
                self.visibility_changed.emit()
        scheme = action.scheme_at(frame)
        if scheme is not None:
            self.set_option("palette", scheme)
        self.frame_changed.emit(self.frame)

    def _show_pose(self, pose: ActionPose) -> None:
        """Bind the motion an action frame plays and pose its frame."""
        motion, clip = pose.motion, pose.clip
        if self.motion is not motion or self.bound is None:
            self.motion = motion
            self.motion_name = motion.name
            self.bound = (
                BoundMotion.bind(self.model.skeleton, motion) if self.model else None
            )
            self.clip = clip
            self.motion_changed.emit()
        elif self.clip is not clip:
            self.clip = clip
            self.motion_changed.emit()
        self.frame = pose.frame

    def action_mask(self) -> int | None:
        """The mask the chosen action sets at the current action frame."""
        if self.action is None:
            return None
        return self.action[1].mask_at(self.action_frame)

    def action_clip_name(self) -> str:
        """What the chosen action plays at its current frame, for display."""
        if self.action is None:
            return ""
        file, action = self.action
        found = action.motion_at(self.action_frame)
        if found is None:
            return "no motion"
        res = file.resources[found[0]] if 0 <= found[0] < len(file.resources) else None
        if res is None:
            return "?"
        if res.embedded:
            return f"embedded resource {res.number}"
        return f"clip {res.number:05d} of set {res.set_id}, take frame {found[1]}"

    # -- visibility and options -----------------------------------------------

    def visible_meshes(self) -> list[MeshData]:
        if self.model is None:
            return []
        return [m for m in self.model.meshes if self.visibility.shows(m)]

    def set_group(self, group: int, shown: bool) -> None:
        _toggle(self.visibility.groups, group, shown)
        self.visibility_changed.emit()
        self._leave_preset()

    def set_part(self, part: int, shown: bool) -> None:
        _toggle(self.visibility.parts, part, shown)
        self.visibility_changed.emit()
        self._leave_preset()

    def set_mesh_hidden(self, uid: int, hidden: bool) -> None:
        """Hide one drawable batch, by its ``MeshData.uid``."""
        _toggle(self.visibility.hidden, uid, hidden)
        self.visibility_changed.emit()

    def rest_visibility(self, model: Model) -> tuple[set[int], set[int]]:
        """The (groups, parts) of the game's rest preset for a fighter from
        the open ROM, everything for any other model (see
        :meth:`GameData.rest_mask`)."""
        mask = None
        if self.game is not None and self.asset is not None:
            mask = self.game.rest_mask(self.asset)
        return model.rest_visibility(mask)

    def reset_visibility(self) -> None:
        if self.model is not None:
            self.set_action(None)
            self.visibility = Visibility(*self.rest_visibility(self.model), set())
            self.visibility_changed.emit()

    def show_everything(self) -> None:
        if self.model is not None:
            self.set_action(None)
            self.visibility = Visibility(*self.model.everything(), set())
            self.visibility_changed.emit()

    def apply_mask(self, mask: int) -> None:
        """Show exactly what a draw mask enables."""
        if self.model is not None:
            self.set_action(None)
            self._show_mask(mask)

    def _show_mask(self, mask: int) -> None:
        assert self.model is not None
        self.visibility = Visibility(*self.model.visibility_from_mask(mask), set())
        self.visibility_changed.emit()

    def set_option(self, name: str, value: object) -> None:
        if getattr(self.options, name) != value:
            setattr(self.options, name, value)
            self.options_changed.emit()


def _toggle(items: set[int], item: int, present: bool) -> None:
    if present:
        items.add(item)
    else:
        items.discard(item)
