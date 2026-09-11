"""The 3D view: an OpenGL widget drawing the session's model as the game
would, minus the DS's fixed-point quirks.

Rendering is deliberately simple - unlit, texture modulated by vertex colour,
back-face culling per mesh flag - because that is all the game does. Skinning
happens on the CPU with numpy each time the pose changes; models are small
enough that this costs nothing.

The viewport's backing and grid are literal colours rather than palette
roles: they have to read the same against the artwork under either theme
(docs/app/theme.md).
"""

from __future__ import annotations

import ctypes
import logging
import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QSurfaceFormat, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from dbkai.model import scene
from dbkai.model.scene import MeshData
from dbkai.ui.session import Session

log = logging.getLogger(__name__)

BACKGROUND = (0.16, 0.16, 0.17, 1.0)
GRID_COLOR = (0.32, 0.32, 0.34, 1.0)
GRID_AXIS_COLOR = (0.45, 0.45, 0.48, 1.0)
BONE_COLOR = (1.0, 0.75, 0.2, 1.0)
WIRE_COLOR = (0.85, 0.85, 0.9, 1.0)

_VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_position;
layout(location = 1) in vec2 in_uv;
layout(location = 2) in vec3 in_color;
uniform mat4 u_mvp;
out vec2 v_uv;
out vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(in_position, 1.0);
    v_uv = in_uv;
    v_color = in_color;
}
"""

_FRAGMENT_SHADER = """
#version 330 core
in vec2 v_uv;
in vec3 v_color;
uniform sampler2D u_texture;
uniform bool u_use_texture;
uniform bool u_use_color;
uniform vec4 u_tint;
uniform float u_alpha;
out vec4 frag;
void main() {
    vec4 c = u_tint;
    if (u_use_color) c.rgb *= v_color;
    if (u_use_texture) {
        vec4 t = texture(u_texture, v_uv);
        if (t.a < 0.02) discard;
        c *= t;
    }
    c.a *= u_alpha;
    frag = c;
}
"""


def request_surface_format() -> None:
    """Ask for a 3.3 core context app-wide. Must run before the QApplication
    is created, which is why :mod:`dbkai.app` calls it first."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


@dataclass
class Camera:
    """An orbit camera: a target, a distance, and two angles."""

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
        center = (lo + hi) / 2
        radius = float(np.linalg.norm(hi - lo)) / 2 or 1.0
        self.target = center.astype(np.float64)
        self.distance = radius / math.sin(math.radians(self.fov) / 2) * 1.1


@dataclass
class _GpuMesh:
    mesh: MeshData
    vao: int
    vbo: int
    ebo: int
    index_count: int


class Viewport(QOpenGLWidget):
    def __init__(self, session: Session, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.session = session
        self.camera = Camera(np.zeros(3))
        self._gl_ok = False
        self._program = 0
        self._uniforms: dict[str, int] = {}
        self._meshes: list[_GpuMesh] = []
        self._textures: dict[tuple[int, int], int] = {}
        self._grid_vao = 0
        self._grid_vbo = 0
        self._grid_count = 0
        self._bone_vao = 0
        self._bone_vbo = 0
        self._bone_count = 0
        self._last_pos = QPoint()
        self._buttons = Qt.MouseButton.NoButton
        self._dirty_model = True
        self._dirty_pose = True
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        session.model_changed.connect(self._on_model)
        session.motion_changed.connect(self._on_pose)
        session.frame_changed.connect(self._on_pose)
        session.visibility_changed.connect(self.update)
        session.options_changed.connect(self._on_options)
        # Coalesce bursts of frame changes into one repaint.
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(0)
        self._repaint.timeout.connect(self.update)

    # -- session events -------------------------------------------------------

    def _on_model(self) -> None:
        self._dirty_model = True
        self._dirty_pose = True
        model = self.session.model
        if model is not None and model.meshes:
            lo, hi = model.bounds()
            self.camera.fit(lo, hi)
        self._repaint.start()

    def _on_pose(self, *_args: object) -> None:
        self._dirty_pose = True
        self._repaint.start()

    def _on_options(self) -> None:
        self._dirty_model = True  # the palette may have changed
        self._repaint.start()

    def reset_camera(self) -> None:
        self.camera = Camera(np.zeros(3))
        self._on_model()

    # -- GL setup -------------------------------------------------------------

    def initializeGL(self) -> None:  # noqa: N802 - Qt override
        try:
            from OpenGL import GL

            self._program = _build_program(GL)
            for name in (
                "u_mvp",
                "u_texture",
                "u_use_texture",
                "u_use_color",
                "u_tint",
                "u_alpha",
            ):
                self._uniforms[name] = GL.glGetUniformLocation(self._program, name)
            self._build_grid(GL)
            self._bone_vao = GL.glGenVertexArrays(1)
            self._bone_vbo = GL.glGenBuffers(1)
            GL.glBindVertexArray(self._bone_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._bone_vbo)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 12, ctypes.c_void_p(0))
            GL.glBindVertexArray(0)
            self._gl_ok = True
        except Exception:  # noqa: BLE001 - no GL is survivable: the rest of the app still works
            log.exception("OpenGL initialisation failed; the viewport stays empty")
            self._gl_ok = False

    def _build_grid(self, GL) -> None:  # noqa: ANN001, N803
        lines = []
        extent, step = 10, 1
        for i in range(-extent, extent + 1, step):
            lines += [(i, 0, -extent), (i, 0, extent), (-extent, 0, i), (extent, 0, i)]
        data = np.array(lines, dtype=np.float32)
        self._grid_count = len(data)
        self._grid_vao = GL.glGenVertexArrays(1)
        self._grid_vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self._grid_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._grid_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 12, ctypes.c_void_p(0))
        GL.glBindVertexArray(0)

    def _upload_model(self, GL) -> None:  # noqa: ANN001, N803
        self._free_model(GL)
        model = self.session.model
        if model is None:
            return
        for mesh in model.meshes:
            if mesh.vertex_count == 0 or mesh.face_count == 0:
                continue
            vao = GL.glGenVertexArrays(1)
            vbo = GL.glGenBuffers(1)
            ebo = GL.glGenBuffers(1)
            GL.glBindVertexArray(vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            data = _interleave(mesh, mesh.positions)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_DYNAMIC_DRAW)
            stride = 8 * 4
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(
                0, 3, GL.GL_FLOAT, False, stride, ctypes.c_void_p(0)
            )
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(
                1, 2, GL.GL_FLOAT, False, stride, ctypes.c_void_p(12)
            )
            GL.glEnableVertexAttribArray(2)
            GL.glVertexAttribPointer(
                2, 3, GL.GL_FLOAT, False, stride, ctypes.c_void_p(20)
            )
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
            idx = np.ascontiguousarray(mesh.indices.astype(np.uint32).reshape(-1))
            GL.glBufferData(
                GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW
            )
            GL.glBindVertexArray(0)
            self._meshes.append(_GpuMesh(mesh, vao, vbo, ebo, len(idx)))
        palette = self.session.options.palette
        for t in model.textures:
            if not t.available:
                continue
            p = min(palette, t.palette_count - 1)
            try:
                rgba = t.rgba(p)
            except Exception:  # noqa: BLE001 - a broken texture shows as untextured
                log.exception("decoding texture %s", t.name)
                continue
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST
            )
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D,
                0,
                GL.GL_RGBA8,
                rgba.width,
                rgba.height,
                0,
                GL.GL_RGBA,
                GL.GL_UNSIGNED_BYTE,
                rgba.pixels,
            )
            self._textures[(t.index, p)] = tex
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def _free_model(self, GL) -> None:  # noqa: ANN001, N803
        for gm in self._meshes:
            GL.glDeleteVertexArrays(1, [gm.vao])
            GL.glDeleteBuffers(2, [gm.vbo, gm.ebo])
        self._meshes = []
        if self._textures:
            GL.glDeleteTextures(len(self._textures), list(self._textures.values()))
        self._textures = {}

    def _update_pose(self, GL) -> None:  # noqa: ANN001, N803
        model = self.session.model
        if model is None:
            return
        world = self.session.world_matrices()
        skin = None
        if world is not None:
            world = self._billboard(world)
            skin = model.skeleton.skin_matrices(world)
        for gm in self._meshes:
            pos = gm.mesh.positions if skin is None else scene.skin(gm.mesh, skin)
            data = _interleave(gm.mesh, pos)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, gm.vbo)
            GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, data)
        # Bone lines: parent to child, plus a short tick at each root.
        segments = []
        if world is not None:
            sk = model.skeleton
            for i in range(len(sk)):
                p = sk.parents[i]
                a = world[i][:3, 3]
                b = world[p][:3, 3] if p >= 0 else a + np.array([0, 0.05, 0])
                segments += [a, b]
        data = np.array(segments, dtype=np.float32).reshape(-1, 3)
        self._bone_count = len(data)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._bone_vbo)
        GL.glBufferData(
            GL.GL_ARRAY_BUFFER, max(data.nbytes, 4), data, GL.GL_DYNAMIC_DRAW
        )
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

    def _billboard(self, world: np.ndarray) -> np.ndarray:
        """Bones flagged as billboards (the ``BL_`` hair pieces) keep their
        position and scale but take the camera's orientation, as the game's
        draw routine does for them."""
        model = self.session.model
        if model is None:
            return world
        sk = model.skeleton
        if not any(f & 0x04 for f in sk.flags):
            return world
        world = world.copy()
        face = np.eye(4)
        face[:3, :3] = self.camera.view()[:3, :3].T
        for i, flags in enumerate(sk.flags):
            if flags & 0x04:
                m = world[i]
                scale = float(np.linalg.norm(m[:3, 0])) or 1.0
                b = face.copy()
                b[:3, :3] *= scale
                b[:3, 3] = m[:3, 3]
                world[i] = b
        return world

    # -- drawing --------------------------------------------------------------

    def paintGL(self) -> None:  # noqa: N802 - Qt override
        from OpenGL import GL

        GL.glClearColor(*BACKGROUND)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self._gl_ok:
            return
        if self._dirty_model:
            self._upload_model(GL)
            self._dirty_model = False
            self._dirty_pose = True
        if self._dirty_pose:
            self._update_pose(GL)
            self._dirty_pose = False
        w, h = max(self.width(), 1), max(self.height(), 1)
        ratio = self.devicePixelRatioF()
        GL.glViewport(0, 0, int(w * ratio), int(h * ratio))
        mvp = (self.camera.projection(w / h) @ self.camera.view()).astype(np.float32)
        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(self._uniforms["u_mvp"], 1, GL.GL_TRUE, mvp)
        GL.glUniform1i(self._uniforms["u_texture"], 0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        opts = self.session.options
        if opts.grid:
            self._draw_lines(GL, self._grid_vao, self._grid_count, GRID_COLOR)
        model = self.session.model
        if model is not None:
            visible = {m.uid for m in self.session.visible_meshes()}
            GL.glPolygonMode(
                GL.GL_FRONT_AND_BACK, GL.GL_LINE if opts.wireframe else GL.GL_FILL
            )
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            opaque = [
                gm
                for gm in self._meshes
                if gm.mesh.uid in visible and gm.mesh.alpha >= 31
            ]
            blended = [
                gm
                for gm in self._meshes
                if gm.mesh.uid in visible and gm.mesh.alpha < 31
            ]
            for gm in opaque:
                self._draw_mesh(GL, gm, model)
            GL.glDepthMask(GL.GL_FALSE)
            for gm in blended:
                self._draw_mesh(GL, gm, model)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            GL.glDisable(GL.GL_CULL_FACE)
            if opts.bones and self._bone_count:
                GL.glDisable(GL.GL_DEPTH_TEST)
                self._draw_lines(GL, self._bone_vao, self._bone_count, BONE_COLOR)
                GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glUseProgram(0)

    def _draw_lines(self, GL, vao: int, count: int, color: tuple) -> None:  # noqa: ANN001, N803
        GL.glUniform1i(self._uniforms["u_use_texture"], 0)
        GL.glUniform1i(self._uniforms["u_use_color"], 0)
        GL.glUniform4f(self._uniforms["u_tint"], *color)
        GL.glUniform1f(self._uniforms["u_alpha"], 1.0)
        GL.glBindVertexArray(vao)
        GL.glDrawArrays(GL.GL_LINES, 0, count)
        GL.glBindVertexArray(0)

    def _draw_mesh(self, GL, gm: _GpuMesh, model) -> None:  # noqa: ANN001, N803
        opts = self.session.options
        mesh = gm.mesh
        material = (
            model.materials[mesh.material]
            if mesh.material < len(model.materials)
            else None
        )
        tex = None
        if opts.textures and material is not None and material.texture is not None:
            for (index, _p), handle in self._textures.items():
                if index == material.texture:
                    tex = handle
                    break
        if tex is not None:
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, _wrap(GL, material)
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, _wrap(GL, material, t=True)
            )
        GL.glUniform1i(self._uniforms["u_use_texture"], 1 if tex is not None else 0)
        GL.glUniform1i(self._uniforms["u_use_color"], 1 if opts.vertex_colors else 0)
        if opts.wireframe:
            GL.glUniform4f(self._uniforms["u_tint"], *WIRE_COLOR)
        elif tex is None and material is not None and material.texture is not None:
            GL.glUniform4f(self._uniforms["u_tint"], 0.8, 0.8, 0.8, 1.0)
        else:
            GL.glUniform4f(self._uniforms["u_tint"], 1.0, 1.0, 1.0, 1.0)
        GL.glUniform1f(self._uniforms["u_alpha"], mesh.alpha / 31)
        if opts.culling and not mesh.double_sided and not opts.wireframe:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_FRONT if mesh.inverted else GL.GL_BACK)
            GL.glFrontFace(GL.GL_CCW)
        else:
            GL.glDisable(GL.GL_CULL_FACE)
        GL.glBindVertexArray(gm.vao)
        GL.glDrawElements(
            GL.GL_TRIANGLES, gm.index_count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(0)
        )
        GL.glBindVertexArray(0)
        if tex is not None:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    # -- mouse ----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self._last_pos = event.position().toPoint()
        self._buttons = event.buttons()
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        pos = event.position().toPoint()
        dx = pos.x() - self._last_pos.x()
        dy = pos.y() - self._last_pos.y()
        self._last_pos = pos
        buttons = event.buttons()
        if buttons & Qt.MouseButton.LeftButton:
            self.camera.yaw -= dx * 0.01
            self.camera.pitch = max(-1.5, min(1.5, self.camera.pitch + dy * 0.01))
        elif buttons & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton):
            view = self.camera.view()
            right, up = view[0, :3], view[1, :3]
            k = self.camera.distance * 0.002
            self.camera.target = self.camera.target - right * dx * k + up * dy * k
        else:
            return
        self._dirty_pose = self._has_billboards()
        self.update()

    def _has_billboards(self) -> bool:
        model = self.session.model
        return model is not None and any(f & 0x04 for f in model.skeleton.flags)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        steps = event.angleDelta().y() / 120
        self.camera.distance = max(0.05, self.camera.distance * (0.9**steps))
        self.update()

    def grab_image(self):  # noqa: ANN201 - QImage
        return self.grabFramebuffer()


def _interleave(mesh: MeshData, positions: np.ndarray) -> np.ndarray:
    data = np.empty((mesh.vertex_count, 8), dtype=np.float32)
    data[:, 0:3] = positions
    data[:, 3:5] = mesh.uvs
    data[:, 5:8] = mesh.colors
    return np.ascontiguousarray(data)


def _wrap(GL, material, t: bool = False) -> int:  # noqa: ANN001, N803
    repeat = material.repeat_t if t else material.repeat_s
    flip = material.flip_t if t else material.flip_s
    if flip:
        return GL.GL_MIRRORED_REPEAT
    return GL.GL_REPEAT if repeat else GL.GL_CLAMP_TO_EDGE


def _build_program(GL) -> int:  # noqa: ANN001, N803
    def compile_shader(kind: int, source: str) -> int:
        shader = GL.glCreateShader(kind)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
        return shader

    program = GL.glCreateProgram()
    vs = compile_shader(GL.GL_VERTEX_SHADER, _VERTEX_SHADER)
    fs = compile_shader(GL.GL_FRAGMENT_SHADER, _FRAGMENT_SHADER)
    GL.glAttachShader(program, vs)
    GL.glAttachShader(program, fs)
    GL.glLinkProgram(program)
    if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(program).decode())
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    return program
