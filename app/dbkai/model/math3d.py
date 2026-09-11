"""Small numpy helpers for 4x4 column-vector transforms.

Every matrix here is a ``(4, 4)`` float64 array applied as ``M @ [x, y, z,
1]``. The DS stores its 4x3 matrices for *row* vectors (``[x y z 1] @ M``),
so :func:`from_ds` transposes on the way in.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

Mat4 = np.ndarray


def quat_to_matrix(q: Sequence[float]) -> Mat4:
    """Rotation matrix of the unit quaternion ``(x, y, z, w)``. The quaternion
    is normalised first, since the files store it in 12 bits."""
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5
    if n == 0:
        return np.eye(4)
    x, y, z, w = x / n, y / n, z / n, w / n
    m = np.eye(4)
    m[0, 0] = 1 - 2 * (y * y + z * z)
    m[0, 1] = 2 * (x * y - z * w)
    m[0, 2] = 2 * (x * z + y * w)
    m[1, 0] = 2 * (x * y + z * w)
    m[1, 1] = 1 - 2 * (x * x + z * z)
    m[1, 2] = 2 * (y * z - x * w)
    m[2, 0] = 2 * (x * z - y * w)
    m[2, 1] = 2 * (y * z + x * w)
    m[2, 2] = 1 - 2 * (x * x + y * y)
    return m


def matrix_to_quat(m: np.ndarray) -> tuple[float, float, float, float]:
    """Unit quaternion ``(x, y, z, w)`` of the rotation part of ``m`` (its
    top-left 3x3, so a 3x3 or a 4x4), which must be orthonormal (no scale)."""
    r = m[:3, :3]
    t = np.trace(r)
    if t > 0:
        s = (t + 1.0) ** 0.5 * 2
        w = 0.25 * s
        x = (r[2, 1] - r[1, 2]) / s
        y = (r[0, 2] - r[2, 0]) / s
        z = (r[1, 0] - r[0, 1]) / s
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = (1.0 + r[0, 0] - r[1, 1] - r[2, 2]) ** 0.5 * 2
        w = (r[2, 1] - r[1, 2]) / s
        x = 0.25 * s
        y = (r[0, 1] + r[1, 0]) / s
        z = (r[0, 2] + r[2, 0]) / s
    elif r[1, 1] > r[2, 2]:
        s = (1.0 + r[1, 1] - r[0, 0] - r[2, 2]) ** 0.5 * 2
        w = (r[0, 2] - r[2, 0]) / s
        x = (r[0, 1] + r[1, 0]) / s
        y = 0.25 * s
        z = (r[1, 2] + r[2, 1]) / s
    else:
        s = (1.0 + r[2, 2] - r[0, 0] - r[1, 1]) ** 0.5 * 2
        w = (r[1, 0] - r[0, 1]) / s
        x = (r[0, 2] + r[2, 0]) / s
        y = (r[1, 2] + r[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w])
    q /= np.linalg.norm(q)
    return tuple(float(v) for v in q)


def from_ds(values: Sequence[float]) -> Mat4:
    """A DS 4x3 matrix (three rows of the 3x3 part, then the translation row,
    for row vectors) as a column-vector 4x4."""
    m = np.eye(4)
    m[:3, :3] = np.array(values[:9]).reshape(3, 3).T
    m[:3, 3] = values[9:12]
    return m


def to_ds(m: Mat4) -> list[float]:
    """The inverse of :func:`from_ds`: the twelve values the DS would take."""
    return [*m[:3, :3].T.reshape(9).tolist(), *m[:3, 3].tolist()]


def transform_points(m: Mat4, points: np.ndarray) -> np.ndarray:
    """Apply ``m`` to an ``(N, 3)`` array of points."""
    return points @ m[:3, :3].T + m[:3, 3]


def decompose(
    m: Mat4,
) -> tuple[np.ndarray, tuple[float, float, float, float], np.ndarray]:
    """Translation, rotation quaternion and scale of an affine matrix, such
    that ``m = T @ R @ S``. A mirroring matrix gets a negative x scale."""
    t = m[:3, 3].copy()
    r = m[:3, :3].copy()
    s = np.linalg.norm(r, axis=0)
    s[s == 0] = 1
    r = r / s
    if np.linalg.det(r) < 0:
        s[0] = -s[0]
        r[:, 0] = -r[:, 0]
    return t, matrix_to_quat(r), s
