"""The viewport's camera maths, and its GL bookkeeping against a fake GL
module (the offscreen platform has no OpenGL to draw with)."""

import math

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from dbkai.formats import dse
from dbkai.model import scene
from dbkai.model.animation import Motion
from dbkai.ui import gl_scene, viewport
from dbkai.ui.camera import BILLBOARD, Camera, face_camera
from dbkai.ui.session import Session
from tests.dse_fixture import build_model, build_motion

# -- camera -------------------------------------------------------------------


def test_fit_frames_the_box():
    cam = Camera(np.zeros(3))
    cam.fit(np.array([-1.0, 0.0, -1.0]), np.array([1.0, 2.0, 1.0]))
    assert np.allclose(cam.target, [0, 1, 0])
    radius = math.sqrt(12) / 2
    assert math.isclose(cam.distance, radius / math.sin(math.radians(22.5)) * 1.1)


def test_view_puts_the_target_straight_ahead():
    cam = Camera(np.array([1.0, 2.0, 3.0]), distance=5.0, yaw=0.3, pitch=-0.4)
    ahead = cam.view() @ np.array([1.0, 2.0, 3.0, 1.0])
    assert np.allclose(ahead[:3], [0, 0, -5])


def test_orbit_pan_and_zoom():
    cam = Camera(np.zeros(3), distance=10.0, yaw=0.0, pitch=0.0)
    cam.orbit(0, 1000)
    assert cam.pitch == 1.5  # clamped short of straight down
    cam.pitch = 0.0
    cam.pan(10, 0)  # drag right: the target moves left, the scene right
    assert cam.target[0] < 0 and math.isclose(cam.target[1], 0, abs_tol=1e-12)
    cam.zoom(1)
    assert math.isclose(cam.distance, 9.0)
    cam.zoom(1000)
    assert cam.distance == 0.05


def test_billboards_face_the_camera_and_keep_place_and_scale():
    world = np.stack([np.eye(4), np.eye(4)])
    world[1][:3, :3] *= 2.0
    world[1][:3, 3] = [1, 2, 3]
    cam = Camera(np.zeros(3), yaw=0.7, pitch=0.2)
    out = face_camera(world, [0, BILLBOARD], cam.view())
    assert np.allclose(out[0], np.eye(4))
    assert np.allclose(out[1][:3, :3], 2.0 * cam.view()[:3, :3].T)
    assert np.allclose(out[1][:3, 3], [1, 2, 3])
    assert face_camera(world, [0, 0], cam.view()) is world


# -- GL bookkeeping -----------------------------------------------------------


class FakeGL:
    """Records every ``gl*`` call; ``glGen*``/``glCreate*`` hand out fresh
    names, ``glGet*`` reports success, ``GL_*`` constants are distinct
    bits."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._name = 0
        self._constants: dict[str, int] = {}

    def __getattr__(self, name: str):
        if name.startswith("GL_"):
            return self._constants.setdefault(name, 1 << len(self._constants))

        def call(*args):
            self.calls.append((name, args))
            if name.startswith(("glGen", "glCreate")):
                self._name += 1
                return self._name
            if name.startswith("glGet"):
                return 1
            return None

        return call

    def called(self, name: str) -> list[tuple]:
        return [args for n, args in self.calls if n == name]


@pytest.fixture
def gl(monkeypatch):
    fake = FakeGL()
    monkeypatch.setattr(viewport, "_load_gl", lambda: fake)
    return fake


@pytest.fixture
def view(qtbot, gl):
    session = Session()
    widget = viewport.Viewport(session)
    qtbot.addWidget(widget)
    session._set_model(scene.build(dse.parse(build_model()), "fixture"), None, None)
    widget.initializeGL()
    widget.paintGL()
    gl.calls.clear()
    return widget


def test_a_new_context_does_not_delete_the_old_contexts_names(view, gl):
    old = {gm.vao for gm in view._meshes}
    assert old
    view.initializeGL()  # Qt made a new context: the old names are gone
    view.paintGL()
    new = {gm.vao for gm in view._meshes}
    assert len(new) == len(old) and not new & old  # uploaded again
    assert not gl.called("glDeleteVertexArrays")
    view.session._set_model(scene.build(dse.parse(build_model()), "again"), None, None)
    view.paintGL()
    deleted = {names[0] for _count, names in gl.called("glDeleteVertexArrays")}
    assert deleted == new


def test_a_palette_change_uploads_only_the_textures(view, gl):
    view.session.set_option("palette", 1)
    view.paintGL()
    assert gl.called("glTexImage2D") and not gl.called("glGenVertexArrays")
    gl.calls.clear()
    view.session.set_option("grid", False)
    view.paintGL()
    assert not gl.called("glTexImage2D") and not gl.called("glBufferData")


def test_a_drag_does_not_drop_a_pending_pose(view, gl):
    view.session.set_motion(Motion(dse.parse(build_motion(frames=3)), "spin"))
    drag = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mouseMoveEvent(drag)
    view.paintGL()
    assert gl.called("glBufferSubData")


def test_billboards_repose_when_the_camera_turns(view, gl):
    view.camera.yaw += 1.0
    view.paintGL()
    assert not gl.called("glBufferSubData")  # nothing faces the camera
    view.session.model.skeleton.flags[1] |= BILLBOARD
    view.camera.yaw += 1.0  # as the screenshot hook does, from outside
    view.paintGL()
    assert gl.called("glBufferSubData")


def test_an_empty_line_buffer_points_gl_at_nothing():
    gl = FakeGL()
    lines = gl_scene.LineBuffer.create(gl)
    lines.upload(gl, np.empty((0, 3)), gl.GL_DYNAMIC_DRAW)
    (_target, size, data, _usage) = gl.called("glBufferData")[0]
    assert size == 0 and data is None and lines.count == 0
