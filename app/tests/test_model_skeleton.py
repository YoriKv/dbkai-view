"""The bind pose of a scaled or mirrored bone, and the bounds of a model
with no vertices, on synthetic data."""

import numpy as np
import pytest

from dbkai.formats import dse
from dbkai.model import math3d, scene
from dbkai.model.skeleton import LocalPose, Skeleton
from tests.dse_fixture import build_model


def _skeleton(tip_bind_world: np.ndarray) -> Skeleton:
    """A root at the origin and a tip whose bind (world) matrix is given."""
    inverse = np.array([np.eye(4), np.linalg.inv(tip_bind_world)])
    return Skeleton(["root", "tip"], [1, 2], [-1, 0], [0, 0], inverse)


@pytest.mark.parametrize(
    "tip_scale", [(2.0, 2.0, 2.0), (1.5, 0.5, 1.0), (-1.0, 1.0, 1.0)]
)
def test_bind_pose_keeps_a_bones_scale(tip_scale):
    # A few props bind a bone with a scale (a planet at 10x), and ground
    # pieces with a mirror. The bind pose must rebuild the bind matrices
    # exactly, or a bone the motion leaves alone shrinks or flips.
    bind = math3d.from_ds([0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 2, 0]) @ np.diag(
        [*tip_scale, 1.0]
    )
    sk = _skeleton(bind)
    rest = sk.bind_pose()
    assert rest[1].scale == pytest.approx(tip_scale)
    world = sk.world_matrices([p.matrix() for p in rest])
    assert np.allclose(world, sk.bind_world)


def test_local_pose_is_translate_rotate_scale():
    quarter = (0.0, 0.0, 2**-0.5, 2**-0.5)  # 90 degrees about z
    m = LocalPose(quarter, (1.0, 0.0, 0.0), (2.0, 1.0, 1.0)).matrix()
    # x is scaled first (to 2), then turned onto +y, then moved by +1 in x.
    assert math3d.transform_points(m, np.array([[1.0, 0, 0]]))[0] == pytest.approx(
        [1.0, 2.0, 0.0]
    )
    assert LocalPose(quarter, (0.0, 0.0, 0.0)).scale == (1.0, 1.0, 1.0)


def test_bind_pose_is_a_fresh_list_each_time():
    sk = scene.build(dse.parse(build_model())).skeleton
    first = sk.bind_pose()
    first.clear()
    assert len(sk.bind_pose()) == len(sk)


def test_bounds_of_a_model_without_vertices_are_zero():
    # Every mesh of the model is empty (an entry that points at an end
    # chunk): the bounds are zeros rather than an error.
    model = scene.build(dse.parse(build_model()))
    for m in model.meshes:
        m.positions = m.positions[:0]
    lo, hi = model.bounds()
    assert lo.tolist() == [0, 0, 0] and hi.tolist() == [0, 0, 0]


def test_bounds_skip_empty_meshes():
    model = scene.build(dse.parse(build_model()))
    lo, hi = model.bounds()
    model.meshes[1].positions = model.meshes[1].positions[:0]
    body = model.meshes[0].positions
    assert model.bounds()[0] == pytest.approx(body.min(axis=0))
    assert model.bounds()[1] == pytest.approx(body.max(axis=0))
    assert np.all(lo <= body.min(axis=0)) and np.all(hi >= body.max(axis=0))
