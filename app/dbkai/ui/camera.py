"""The viewport's orbit camera, and the billboard bones that face it.

Plain numpy: nothing here needs Qt or OpenGL, so the maths is testable
headless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: The bone flag of a billboard (the ``BL_`` hair pieces).
BILLBOARD = 0x04


@dataclass
class Camera:
    """An orbit camera: a target, a distance, and two angles (radians)."""

    target: np.ndarray
    distance: float = 10.0
    yaw: float = 0.6
    pitch: float = 0.25
    fov: float = 45.0

    def eye(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        return self.target + self.distance * np.array(
            [cp * math.sin(self.yaw), math.sin(self.pitch), cp * math.cos(self.yaw)]
        )

    def view(self) -> np.ndarray:
        eye = self.eye()
        f = self.target - eye
        f /= np.linalg.norm(f)
        up = np.array([0.0, 1.0, 0.0])
        s = np.cross(f, up)
        if np.linalg.norm(s) < 1e-6:
            s = np.array([1.0, 0.0, 0.0])
        s /= np.linalg.norm(s)
        u = np.cross(s, f)
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = s, u, -f
        m[:3, 3] = -m[:3, :3] @ eye
        return m

    def projection(self, aspect: float) -> np.ndarray:
        near = max(self.distance * 0.01, 0.01)
        far = self.distance * 20 + 100
        f = 1.0 / math.tan(math.radians(self.fov) / 2)
        m = np.zeros((4, 4))
        m[0, 0] = f / aspect
        m[1, 1] = f
        m[2, 2] = (far + near) / (near - far)
        m[2, 3] = 2 * far * near / (near - far)
        m[3, 2] = -1
        return m

    def fit(self, lo: np.ndarray, hi: np.ndarray) -> None:
        """Aim at the middle of a bounding box, far enough back to see all
        of it."""
        center = (lo + hi) / 2
        radius = float(np.linalg.norm(hi - lo)) / 2 or 1.0
        self.target = center.astype(np.float64)
        self.distance = radius / math.sin(math.radians(self.fov) / 2) * 1.1

    def orbit(self, dx: float, dy: float) -> None:
        """Turn around the target by a mouse drag of ``dx, dy`` pixels."""
        self.yaw -= dx * 0.01
        self.pitch = max(-1.5, min(1.5, self.pitch + dy * 0.01))

    def pan(self, dx: float, dy: float) -> None:
        """Slide the target so the scene follows a drag of ``dx, dy``
        pixels, at a rate that scales with the distance."""
        view = self.view()
        right, up = view[0, :3], view[1, :3]
        k = self.distance * 0.002
        self.target = self.target - right * dx * k + up * dy * k

    def zoom(self, steps: float) -> None:
        """Move in by 10% per wheel step (out for negative steps)."""
        self.distance = max(0.05, self.distance * (0.9**steps))

    @property
    def orientation(self) -> tuple[float, float]:
        """What a billboard depends on: the angles, not the target or the
        distance."""
        return self.yaw, self.pitch


def has_billboards(flags: list[int]) -> bool:
    return any(f & BILLBOARD for f in flags)


def face_camera(world: np.ndarray, flags: list[int], view: np.ndarray) -> np.ndarray:
    """Bones flagged as billboards keep their position and scale but take
    the camera's orientation, as the game's draw routine does for them.
    Returns ``world`` itself when there are none, a changed copy otherwise."""
    if not has_billboards(flags):
        return world
    world = world.copy()
    face = np.eye(4)
    face[:3, :3] = view[:3, :3].T
    for i, bone_flags in enumerate(flags):
        if bone_flags & BILLBOARD:
            m = world[i]
            scale = float(np.linalg.norm(m[:3, 0])) or 1.0
            b = face.copy()
            b[:3, :3] *= scale
            b[:3, 3] = m[:3, 3]
            world[i] = b
    return world
