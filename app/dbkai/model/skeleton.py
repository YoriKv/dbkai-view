"""The bone hierarchy of a model and the poses applied to it."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dbkai.formats import dse
from dbkai.model import math3d
from dbkai.model.math3d import Mat4


@dataclass(frozen=True)
class LocalPose:
    """One bone's transform relative to its parent, ``T @ R @ S``. A motion
    frame has no scale; a bind pose can (a few props scale a bone)."""

    rotation: tuple[float, float, float, float]  # x, y, z, w
    translation: tuple[float, float, float]
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def matrix(self) -> Mat4:
        m = math3d.quat_to_matrix(self.rotation)
        m[:3, :3] *= self.scale
        m[:3, 3] = self.translation
        return m


@dataclass
class Skeleton:
    """Bones in file order with their bind pose.

    ``inverse_bind[i]`` maps model space into bone *i*'s space, exactly as the
    file stores it; ``bind_world`` is its inverse and ``bind_local`` the same
    relative to the parent, which is what a bone with no animation track
    keeps; :meth:`bind_pose` is ``bind_local`` as poses. Every parent
    precedes its children in ``order``.
    """

    names: list[str]
    hashes: list[int]
    parents: list[int]
    flags: list[int]
    inverse_bind: np.ndarray  # (N, 4, 4)
    bind_world: np.ndarray = field(init=False)
    bind_local: np.ndarray = field(init=False)
    order: list[int] = field(init=False)
    _bind_pose: list[LocalPose] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        n = len(self.names)
        self.bind_world = np.array([_invert(m) for m in self.inverse_bind]).reshape(
            n, 4, 4
        )
        self.order = _topological(self.parents)
        local = np.empty_like(self.bind_world)
        for i in range(n):
            p = self.parents[i]
            local[i] = (
                self.bind_world[i]
                if p < 0
                else self.inverse_bind[p] @ self.bind_world[i]
            )
        self.bind_local = local
        self._bind_pose = [_pose_of(m) for m in local]

    def __len__(self) -> int:
        return len(self.names)

    @classmethod
    def from_dse(cls, file: dse.DseFile) -> Skeleton:
        return cls(
            names=[b.name for b in file.bones],
            hashes=[b.name_hash for b in file.bones],
            parents=[b.parent for b in file.bones],
            flags=[b.flags for b in file.bones],
            inverse_bind=np.array(
                [math3d.from_ds(b.inverse_bind.values) for b in file.bones]
            ).reshape(len(file.bones), 4, 4),
        )

    def bind_pose(self) -> list[LocalPose]:
        """The bind pose as local transforms, for a bone the animation leaves
        alone. Worked out once: a player asks for it every frame."""
        return list(self._bind_pose)

    def world_matrices(self, local: list[Mat4] | np.ndarray) -> np.ndarray:
        """Forward kinematics: the world matrix of every bone from its local
        one, parents first."""
        world = np.empty((len(self), 4, 4))
        for i in self.order:
            p = self.parents[i]
            world[i] = local[i] if p < 0 else world[p] @ local[i]
        return world

    def skin_matrices(self, world: np.ndarray) -> np.ndarray:
        """``world @ inverse_bind`` per bone: what moves a model-space vertex
        bound to that bone to where it is now."""
        return world @ self.inverse_bind

    def children(self, bone: int) -> list[int]:
        return [i for i, p in enumerate(self.parents) if p == bone]


def _pose_of(local: Mat4) -> LocalPose:
    t, q, s = math3d.decompose(local)
    return LocalPose(q, tuple(t.tolist()), tuple(s.tolist()))


def _invert(m: Mat4) -> Mat4:
    """The inverse of a bind matrix; a degenerate one (a few effect models
    store all zeros) counts as the identity so the file still loads."""
    try:
        inv = np.linalg.inv(m)
    except np.linalg.LinAlgError:
        return np.eye(4)
    return inv if np.all(np.isfinite(inv)) else np.eye(4)


def _topological(parents: list[int]) -> list[int]:
    """Bone indices with every parent before its children. The files list
    them that way already; this only makes it a guarantee."""
    order: list[int] = []
    seen: set[int] = set()

    def visit(i: int) -> None:
        if i in seen:
            return
        p = parents[i]
        if 0 <= p < len(parents) and p != i:
            visit(p)
        seen.add(i)
        order.append(i)

    for i in range(len(parents)):
        visit(i)
    return order
