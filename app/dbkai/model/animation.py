"""Motion data bound to a model's skeleton.

A motion file (``sm_*.dse``, or a single-motion ``.dse``) carries its own bone
list; a model's bones are matched to it by name hash, then by name. A bone
with no counterpart keeps its bind pose.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from dbkai.formats import dse
from dbkai.model.skeleton import LocalPose, Skeleton


@dataclass(frozen=True)
class Clip:
    """A named run of frames in a motion file. ``first`` is the take frame
    number (1-based, as the file records it) of the run's first frame, which
    is how the action files address frames inside a clip."""

    name: str
    start: int
    frame_count: int
    first: int = 1

    @property
    def end(self) -> int:
        return self.start + self.frame_count

    @property
    def number(self) -> int | None:
        """The leading number of the clip's name (``00010_..`` -> 10), which
        the game matches clips by."""
        digits = self.name.split("_", 1)[0]
        return int(digits) if digits.isdigit() else None

    def frame_for_take(self, take_frame: int) -> int:
        """The file frame for a take frame, clamped into the clip."""
        rel = max(0, min(take_frame - self.first, self.frame_count - 1))
        return self.start + rel


@dataclass
class Motion:
    """A parsed motion file with its clips. ``clips`` is the file's own table,
    or one clip spanning every frame when it has none."""

    file: dse.DseFile
    name: str

    @cached_property
    def clips(self) -> list[Clip]:
        if self.file.animations:
            return [
                Clip(
                    a.name,
                    a.start,
                    min(a.frame_count, self.file.frame_count - a.start),
                    a.first,
                )
                for a in self.file.animations
            ]
        return [Clip(self.name, 0, self.file.frame_count)]

    @property
    def frame_count(self) -> int:
        return self.file.frame_count

    def clip_by_number(self, number: int) -> Clip | None:
        for c in self.clips:
            if c.number == number:
                return c
        return None

    def bone_index(self, name_hash: int, name: str) -> int | None:
        """The motion bone for a model bone: by name, then by hash. Names
        come first because the 16-bit hashes collide (``c_waist_geo1`` and
        ``r_elbow_geo1`` share one)."""
        by_name = self.file.bone_by_name.get(name)
        if by_name is not None:
            return by_name.index
        by_hash = self.file.bone_by_hash.get(name_hash)
        return None if by_hash is None else by_hash.index


@dataclass
class BoundMotion:
    """``motion`` matched against ``skeleton``: ``mapping[i]`` is the motion
    bone that drives model bone *i*, or -1."""

    skeleton: Skeleton
    motion: Motion
    mapping: list[int]

    @classmethod
    def bind(cls, skeleton: Skeleton, motion: Motion) -> BoundMotion:
        mapping = []
        for h, name in zip(skeleton.hashes, skeleton.names, strict=True):
            found = motion.bone_index(h, name)
            mapping.append(-1 if found is None else found)
        return cls(skeleton, motion, mapping)

    @property
    def matched(self) -> int:
        return sum(1 for m in self.mapping if m >= 0)

    def local_poses(
        self, frame: int, rest: list[LocalPose] | None = None
    ) -> list[LocalPose]:
        """Every model bone's local pose in ``frame``."""
        rest = rest if rest is not None else self.skeleton.bind_pose()
        out = []
        for i, m in enumerate(self.mapping):
            if m < 0:
                out.append(rest[i])
            else:
                p = self.motion.file.pose(frame, m)
                out.append(LocalPose(p.rotation, p.translation))
        return out

    def world_matrices(
        self, frame: int, rest: list[LocalPose] | None = None
    ) -> np.ndarray:
        local = [p.matrix() for p in self.local_poses(frame, rest)]
        return self.skeleton.world_matrices(local)
