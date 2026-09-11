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

from dbkai.formats import dse
from dbkai.game import Asset, AssetKind, GameData, unwrap
from dbkai.model import scene
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
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // FRAMES_PER_SECOND)
        self._timer.timeout.connect(self._tick)
        self.speed = 1.0
        self._accumulator = 0.0
        self.loop = True

    # -- opening things -------------------------------------------------------

    def open_rom(self, path: str | Path) -> None:
        path = Path(path)
        self.game = GameData.open(path)
        self.game_path = path
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
        file = self.game.load_dse(asset)
        if not file.meshes and file.frame_count:
            self.set_motion(Motion(file, asset.name))
            return
        self._set_model(self.game.load_model(asset), asset, None)

    def _set_model(self, model: Model, asset: Asset | None, path: Path | None) -> None:
        self.stop()
        self.model = model
        self.asset = asset
        self.model_path = path
        groups, parts = model.default_visibility()
        self.visibility = Visibility(groups, parts, set())
        self.motion = None
        self.motion_name = ""
        self.bound = None
        self.clip = None
        self.frame = 0
        self.model_changed.emit()
        self.motion_changed.emit()
        self.visibility_changed.emit()
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
        self.stop()
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
        self.set_frame(index + (self.clip.start if self.clip else 0))

    def play(self) -> None:
        if self.clip is None or self._timer.isActive():
            return
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

    def _tick(self) -> None:
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

    # -- visibility and options -----------------------------------------------

    def visible_meshes(self) -> list[MeshData]:
        if self.model is None:
            return []
        return [m for m in self.model.meshes if self.visibility.shows(m)]

    def set_group(self, group: int, shown: bool) -> None:
        _toggle(self.visibility.groups, group, shown)
        self.visibility_changed.emit()

    def set_part(self, part: int, shown: bool) -> None:
        _toggle(self.visibility.parts, part, shown)
        self.visibility_changed.emit()

    def set_mesh_hidden(self, uid: int, hidden: bool) -> None:
        """Hide one drawable batch, by its ``MeshData.uid``."""
        _toggle(self.visibility.hidden, uid, hidden)
        self.visibility_changed.emit()

    def reset_visibility(self) -> None:
        if self.model is not None:
            groups, parts = self.model.default_visibility()
            self.visibility = Visibility(groups, parts, set())
            self.visibility_changed.emit()

    def show_everything(self) -> None:
        if self.model is not None:
            self.visibility = Visibility(
                set(self.model.groups), set(self.model.parts), set()
            )
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
