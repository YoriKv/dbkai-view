"""The OpenGL objects the viewport draws with: the shader program, a mesh's
vertex buffers, line buffers and textures.

Every function takes the ``OpenGL.GL`` module as ``GL`` and expects the
viewport's context to be current; none of them imports PyOpenGL, so the
module loads (and the viewport's logic tests) without a GL driver.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import numpy as np

from dbkai.formats.texture import Rgba
from dbkai.model.scene import MaterialData, MeshData

VERTEX_SHADER = """
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

FRAGMENT_SHADER = """
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

#: The program's uniforms, looked up once after linking.
UNIFORMS = ("u_mvp", "u_texture", "u_use_texture", "u_use_color", "u_tint", "u_alpha")

#: Floats per interleaved vertex: position (3), uv (2), colour (3).
_VERTEX_FLOATS = 8


def build_program(GL) -> int:  # noqa: ANN001, N803
    def compile_shader(kind: int, source: str) -> int:
        shader = GL.glCreateShader(kind)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
        return shader

    program = GL.glCreateProgram()
    vs = compile_shader(GL.GL_VERTEX_SHADER, VERTEX_SHADER)
    fs = compile_shader(GL.GL_FRAGMENT_SHADER, FRAGMENT_SHADER)
    GL.glAttachShader(program, vs)
    GL.glAttachShader(program, fs)
    GL.glLinkProgram(program)
    if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(program).decode())
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    return program


# -- meshes -------------------------------------------------------------------


@dataclass
class GpuMesh:
    """A mesh's vertex array: interleaved vertices, re-uploaded as the pose
    changes, and its fixed triangle indices."""

    mesh: MeshData
    vao: int
    vbo: int
    ebo: int
    index_count: int

    @classmethod
    def create(cls, GL, mesh: MeshData) -> GpuMesh:  # noqa: ANN001, N803
        vao = GL.glGenVertexArrays(1)
        vbo = GL.glGenBuffers(1)
        ebo = GL.glGenBuffers(1)
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        data = interleave(mesh, mesh.positions)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_DYNAMIC_DRAW)
        stride = _VERTEX_FLOATS * 4
        for location, size, offset in ((0, 3, 0), (1, 2, 12), (2, 3, 20)):
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(
                location, size, GL.GL_FLOAT, False, stride, ctypes.c_void_p(offset)
            )
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
        idx = np.ascontiguousarray(mesh.indices.astype(np.uint32).reshape(-1))
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
        GL.glBindVertexArray(0)
        return cls(mesh, vao, vbo, ebo, len(idx))

    def upload_positions(self, GL, positions: np.ndarray) -> None:  # noqa: ANN001, N803
        data = interleave(self.mesh, positions)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, data)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

    def draw(self, GL) -> None:  # noqa: ANN001, N803
        GL.glBindVertexArray(self.vao)
        GL.glDrawElements(
            GL.GL_TRIANGLES, self.index_count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(0)
        )
        GL.glBindVertexArray(0)

    def delete(self, GL) -> None:  # noqa: ANN001, N803
        GL.glDeleteVertexArrays(1, [self.vao])
        GL.glDeleteBuffers(2, [self.vbo, self.ebo])


def interleave(mesh: MeshData, positions: np.ndarray) -> np.ndarray:
    """One float32 row per vertex: position, uv, colour."""
    data = np.empty((mesh.vertex_count, _VERTEX_FLOATS), dtype=np.float32)
    data[:, 0:3] = positions
    data[:, 3:5] = mesh.uvs
    data[:, 5:8] = mesh.colors
    return data


# -- lines --------------------------------------------------------------------


@dataclass
class LineBuffer:
    """Position-only vertices drawn as ``GL_LINES``: the grid, the bones."""

    vao: int
    vbo: int
    count: int = 0

    @classmethod
    def create(cls, GL) -> LineBuffer:  # noqa: ANN001, N803
        vao = GL.glGenVertexArrays(1)
        vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 12, ctypes.c_void_p(0))
        GL.glBindVertexArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        return cls(vao, vbo)

    def upload(self, GL, points: np.ndarray, usage: int) -> None:  # noqa: ANN001, N803
        """Replace the vertices with ``points``, an (N, 3) array of line
        ends in pairs."""
        data = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 3)
        self.count = len(data)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        # An empty array has no storage to point GL at: allocate nothing.
        GL.glBufferData(
            GL.GL_ARRAY_BUFFER, data.nbytes, data if self.count else None, usage
        )
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

    def draw(self, GL) -> None:  # noqa: ANN001, N803
        if not self.count:
            return
        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_LINES, 0, self.count)
        GL.glBindVertexArray(0)

    def delete(self, GL) -> None:  # noqa: ANN001, N803
        GL.glDeleteVertexArrays(1, [self.vao])
        GL.glDeleteBuffers(1, [self.vbo])


def grid_lines(extent: int = 10) -> np.ndarray:
    """A floor grid of unit squares from ``-extent`` to ``extent``."""
    lines = []
    for i in range(-extent, extent + 1):
        lines += [(i, 0, -extent), (i, 0, extent), (-extent, 0, i), (extent, 0, i)]
    return np.array(lines, dtype=np.float32)


def bone_lines(world: np.ndarray, parents: list[int]) -> np.ndarray:
    """Each bone to its parent, plus a short tick up from each root."""
    segments = []
    for i, p in enumerate(parents):
        a = world[i][:3, 3]
        b = world[p][:3, 3] if p >= 0 else a + np.array([0, 0.05, 0])
        segments += [a, b]
    return np.array(segments, dtype=np.float32).reshape(-1, 3)


# -- textures -----------------------------------------------------------------


def upload_texture(GL, rgba: Rgba) -> int:  # noqa: ANN001, N803
    """A nearest-filtered RGBA8 texture; its wrap mode is set per draw, by
    the material using it."""
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
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
    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
    return tex


def bind_texture(GL, tex: int, material: MaterialData) -> None:  # noqa: ANN001, N803
    """Bind ``tex`` to unit 0 with the material's repeat and flip modes."""
    GL.glActiveTexture(GL.GL_TEXTURE0)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexParameteri(
        GL.GL_TEXTURE_2D,
        GL.GL_TEXTURE_WRAP_S,
        _wrap(GL, material.repeat_s, material.flip_s),
    )
    GL.glTexParameteri(
        GL.GL_TEXTURE_2D,
        GL.GL_TEXTURE_WRAP_T,
        _wrap(GL, material.repeat_t, material.flip_t),
    )


def _wrap(GL, repeat: bool, flip: bool) -> int:  # noqa: ANN001, N803
    if flip:
        return GL.GL_MIRRORED_REPEAT
    return GL.GL_REPEAT if repeat else GL.GL_CLAMP_TO_EDGE
