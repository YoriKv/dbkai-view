"""The 3D view: an OpenGL widget drawing the session's model as the game
would, minus the DS's fixed-point quirks.

Rendering is deliberately simple - unlit, texture modulated by vertex colour,
back-face culling per mesh flag - because that is all the game does. Skinning
happens on the CPU with numpy each time the pose changes; models are small
enough that this costs nothing. The camera is :mod:`dbkai.ui.camera`, the GL
objects :mod:`dbkai.ui.gl_scene`.

The viewport's backing and grid are literal colours rather than palette
roles: they have to read the same against the artwork under either theme
(docs/app/theme.md).
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QMouseEvent, QSurfaceFormat, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from dbkai.model import scene
from dbkai.model.scene import Model
from dbkai.ui import gl_scene
from dbkai.ui.camera import Camera, face_camera, has_billboards
from dbkai.ui.gl_scene import GpuMesh, LineBuffer
from dbkai.ui.session import Session

__all__ = ["Camera", "Viewport", "request_surface_format"]

log = logging.getLogger(__name__)

BACKGROUND = (0.16, 0.16, 0.17, 1.0)
GRID_COLOR = (0.32, 0.32, 0.34, 1.0)
BONE_COLOR = (1.0, 0.75, 0.2, 1.0)
WIRE_COLOR = (0.85, 0.85, 0.9, 1.0)
#: Tint of a textured material drawn with textures turned off.
UNTEXTURED_TINT = (0.8, 0.8, 0.8, 1.0)
WHITE = (1.0, 1.0, 1.0, 1.0)

#: The game's opaque mesh alpha; anything less is blended.
_OPAQUE = 31


def request_surface_format() -> None:
    """Ask for a 3.3 core context app-wide. Must run before the QApplication
    is created, which is why :mod:`dbkai.app` calls it first."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


def _load_gl():  # noqa: ANN202 - the OpenGL.GL module
    """PyOpenGL, imported when the first context exists rather than with
    the module."""
    from OpenGL import GL

    return GL


class Viewport(QOpenGLWidget):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.camera = Camera(np.zeros(3))
        #: The ``OpenGL.GL`` module, once imported, and whether the program
        #: and buffers built in the current context.
        self._gl = None
        self._ready = False
        self._forget_gl_objects()
        self._last_pos = QPoint()
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        session.model_changed.connect(self._on_model)
        session.motion_changed.connect(self._on_pose)
        session.frame_changed.connect(self._on_pose)
        session.visibility_changed.connect(self.update)
        # The palette is compared against the uploaded one when painting.
        session.options_changed.connect(self.update)

    def _forget_gl_objects(self) -> None:
        """Drop every GL name held and mark everything for upload. Right for
        a new context: the old one took its objects with it, and its names
        mean something else (or nothing) in the new one."""
        self._program = 0
        self._uniforms: dict[str, int] = {}
        self._meshes: list[GpuMesh] = []
        #: Texture index to GL texture, decoded with ``_texture_palette``.
        self._textures: dict[int, int] = {}
        self._texture_palette: int | None = None
        self._grid: LineBuffer | None = None
        self._bones: LineBuffer | None = None
        self._dirty_model = True
        self._dirty_pose = True
        #: The camera orientation the billboards were last posed for.
        self._posed_orientation: tuple[float, float] | None = None

    # -- session events -------------------------------------------------------

    def _on_model(self) -> None:
        self._dirty_model = True
        self._dirty_pose = True
        self._fit_camera()
        self.update()

    def _on_pose(self, *_args: object) -> None:
        self._dirty_pose = True
        self.update()

    def _fit_camera(self) -> None:
        model = self.session.model
        if model is not None and model.meshes:
            self.camera.fit(*model.bounds())

    def reset_camera(self) -> None:
        self.camera = Camera(np.zeros(3))
        self._fit_camera()
        self.update()

    # -- GL setup -------------------------------------------------------------

    def initializeGL(self) -> None:  # noqa: N802 - Qt override
        # Runs again when the widget moves to another window and Qt gives it
        # a new context.
        self._ready = False
        self._forget_gl_objects()
        try:
            GL = self._gl = _load_gl()  # noqa: N806
            self._program = gl_scene.build_program(GL)
            self._uniforms = {
                name: GL.glGetUniformLocation(self._program, name)
                for name in gl_scene.UNIFORMS
            }
            self._grid = LineBuffer.create(GL)
            self._grid.upload(GL, gl_scene.grid_lines(), GL.GL_STATIC_DRAW)
            self._bones = LineBuffer.create(GL)
            self._ready = True
        except Exception:  # noqa: BLE001 - no GL is survivable: the rest of the app still works
            log.exception("OpenGL initialisation failed; the viewport stays empty")

    def _upload_model(self, GL, model: Model | None) -> None:  # noqa: ANN001, N803
        for gm in self._meshes:
            gm.delete(GL)
        self._meshes = []
        if model is not None:
            self._meshes = [
                GpuMesh.create(GL, mesh)
                for mesh in model.meshes
                if mesh.vertex_count and mesh.face_count
            ]
        self._upload_textures(GL, model)

    def _upload_textures(self, GL, model: Model | None) -> None:  # noqa: ANN001, N803
        if self._textures:
            GL.glDeleteTextures(len(self._textures), list(self._textures.values()))
        self._textures = {}
        palette = self.session.options.palette
        self._texture_palette = palette
        if model is None:
            return
        for t in model.textures:
            if not t.available:
                continue
            try:
                rgba = t.rgba(min(palette, t.palette_count - 1))
            except Exception:  # noqa: BLE001 - a broken texture shows as untextured
                log.exception("decoding texture %s", t.name)
                continue
            self._textures[t.index] = gl_scene.upload_texture(GL, rgba)

    def _update_pose(self, GL, model: Model | None) -> None:  # noqa: ANN001, N803
        self._posed_orientation = self.camera.orientation
        world = self.session.world_matrices()
        if model is None or world is None:
            self._bones.upload(GL, np.empty((0, 3)), GL.GL_DYNAMIC_DRAW)
            return
        sk = model.skeleton
        world = face_camera(world, sk.flags, self.camera.view())
        skin = sk.skin_matrices(world)
        for gm in self._meshes:
            gm.upload_positions(GL, scene.skin(gm.mesh, skin))
        self._bones.upload(
            GL, gl_scene.bone_lines(world, sk.parents), GL.GL_DYNAMIC_DRAW
        )

    # -- drawing --------------------------------------------------------------

    def paintGL(self) -> None:  # noqa: N802 - Qt override
        # Qt has made the context current and set the viewport to the
        # framebuffer's device-pixel size before calling this.
        GL = self._gl  # noqa: N806
        if GL is None:
            return
        GL.glClearColor(*BACKGROUND)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self._ready:
            return
        model = self.session.model
        opts = self.session.options
        if self._dirty_model:
            self._upload_model(GL, model)
            self._dirty_model = False
            self._dirty_pose = True
        elif self._texture_palette != opts.palette:
            self._upload_textures(GL, model)
        if (
            model is not None
            and self._posed_orientation != self.camera.orientation
            and has_billboards(model.skeleton.flags)
        ):
            self._dirty_pose = True
        if self._dirty_pose:
            self._update_pose(GL, model)
            self._dirty_pose = False
        w, h = max(self.width(), 1), max(self.height(), 1)
        mvp = (self.camera.projection(w / h) @ self.camera.view()).astype(np.float32)
        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(self._uniforms["u_mvp"], 1, GL.GL_TRUE, mvp)
        GL.glUniform1i(self._uniforms["u_texture"], 0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        if opts.grid:
            self._draw_lines(GL, self._grid, GRID_COLOR)
        if model is not None:
            self._draw_meshes(GL, model)
            if opts.bones:
                GL.glDisable(GL.GL_DEPTH_TEST)
                self._draw_lines(GL, self._bones, BONE_COLOR)
                GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glUseProgram(0)

    def _draw_meshes(self, GL, model: Model) -> None:  # noqa: ANN001, N803
        """Opaque meshes first, then the blended ones without depth writes."""
        opts = self.session.options
        visible = {m.uid for m in self.session.visible_meshes()}
        shown = [gm for gm in self._meshes if gm.mesh.uid in visible]
        GL.glPolygonMode(
            GL.GL_FRONT_AND_BACK, GL.GL_LINE if opts.wireframe else GL.GL_FILL
        )
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        for gm in shown:
            if gm.mesh.alpha >= _OPAQUE:
                self._draw_mesh(GL, gm, model)
        GL.glDepthMask(GL.GL_FALSE)
        for gm in shown:
            if gm.mesh.alpha < _OPAQUE:
                self._draw_mesh(GL, gm, model)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        GL.glDisable(GL.GL_CULL_FACE)

    def _draw_lines(self, GL, lines: LineBuffer, color: tuple) -> None:  # noqa: ANN001, N803
        GL.glUniform1i(self._uniforms["u_use_texture"], 0)
        GL.glUniform1i(self._uniforms["u_use_color"], 0)
        GL.glUniform4f(self._uniforms["u_tint"], *color)
        GL.glUniform1f(self._uniforms["u_alpha"], 1.0)
        lines.draw(GL)

    def _draw_mesh(self, GL, gm: GpuMesh, model: Model) -> None:  # noqa: ANN001, N803
        opts = self.session.options
        mesh = gm.mesh
        material = (
            model.materials[mesh.material]
            if mesh.material < len(model.materials)
            else None
        )
        textured = material is not None and material.texture is not None
        tex = self._textures.get(material.texture) if textured else None
        if not opts.textures:
            tex = None
        if tex is not None:
            gl_scene.bind_texture(GL, tex, material)
        GL.glUniform1i(self._uniforms["u_use_texture"], 1 if tex is not None else 0)
        GL.glUniform1i(self._uniforms["u_use_color"], 1 if opts.vertex_colors else 0)
        if opts.wireframe:
            tint = WIRE_COLOR
        elif textured and tex is None:
            tint = UNTEXTURED_TINT
        else:
            tint = WHITE
        GL.glUniform4f(self._uniforms["u_tint"], *tint)
        GL.glUniform1f(self._uniforms["u_alpha"], mesh.alpha / _OPAQUE)
        if opts.culling and not mesh.double_sided and not opts.wireframe:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_FRONT if mesh.inverted else GL.GL_BACK)
            GL.glFrontFace(GL.GL_CCW)
        else:
            GL.glDisable(GL.GL_CULL_FACE)
        gm.draw(GL)
        if tex is not None:
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    # -- mouse ----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self._last_pos = event.position().toPoint()
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        pos = event.position().toPoint()
        dx = pos.x() - self._last_pos.x()
        dy = pos.y() - self._last_pos.y()
        self._last_pos = pos
        buttons = event.buttons()
        if buttons & Qt.MouseButton.LeftButton:
            self.camera.orbit(dx, dy)
        elif buttons & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton):
            self.camera.pan(dx, dy)
        else:
            return
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        self.camera.zoom(event.angleDelta().y() / 120)
        self.update()

    def grab_image(self) -> QImage:
        return self.grabFramebuffer()
