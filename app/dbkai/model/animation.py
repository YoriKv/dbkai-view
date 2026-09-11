"""Motion data bound to a model's skeleton.

A motion file (``sm_*.dse``, or a single-motion ``.dse``) carries its own bone
list; a model's bones are matched to it by name hash, then by name. A bone
with no counterpart keeps its bind pose.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dbkai.formats import dse
from dbkai.model.skeleton import LocalPose, Skeleton


@dataclass(frozen=True)
class Clip:
    """A named run of frames in a motion file."""

    name: str
    start: int
    frame_count: int

    @property
    def end(self) -> int:
        return self.start + self.frame_count


@dataclass
class Motion:
    """A parsed motion file with its clips. ``clips`` is the file's own table,
    or one clip spanning every frame when it has none."""

    file: dse.DseFile
    name: str

    @property
    def clips(self) -> list[Clip]:
        if self.file.animations:
            return [
                Clip(
                    a.name, a.start, min(a.frame_count, self.file.frame_count - a.start)
                )
                for a in self.file.animations
            ]
        return [Clip(self.name, 0, self.file.frame_count)]

    @property
    def frame_count(self) -> int:
        return self.file.frame_count

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
