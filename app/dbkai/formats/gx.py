"""The DS geometry engine's packed command stream, decoded to vertices.

A display list is a sequence of 32-bit words: one word packs up to four
command bytes, followed by each command's parameters in order. The game's
models store only the per-vertex commands (COLOR, TEXCOORD, NORMAL and the
VTX_* family); BEGIN_VTXS and everything else is issued by the CPU. So the
decoder here turns a stream into a flat vertex list and leaves it to the caller
to know the primitive type.

Fixed-point conventions (``docs/formats/gx.md``): vertex coordinates are 4.12,
texture coordinates 12.4 texels, colours 5-bit RGB.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Command byte -> number of parameter words. Only what a display list can hold.
PARAM_COUNTS: dict[int, int] = {
    0x00: 0,  # NOP
    0x20: 1,  # COLOR
    0x21: 1,  # NORMAL
    0x22: 1,  # TEXCOORD
    0x23: 2,  # VTX_16
    0x24: 1,  # VTX_10
    0x25: 1,  # VTX_XY
    0x26: 1,  # VTX_XZ
    0x27: 1,  # VTX_YZ
    0x28: 1,  # VTX_DIFF
    0x29: 1,  # POLYGON_ATTR
    0x2A: 1,  # TEXIMAGE_PARAM
    0x2B: 1,  # PLTT_BASE
    0x40: 1,  # BEGIN_VTXS
    0x41: 0,  # END_VTXS
}


class DisplayListError(ValueError):
    """The stream holds a command the decoder does not know."""


@dataclass(frozen=True)
class Vertex:
    """One submitted vertex in model space. ``uv`` is in the raw 12.4 texel
    units the stream carries (before any texture matrix) and ``color`` is the
    5-bit RGB that was current, or ``None`` if no COLOR preceded it."""

    position: tuple[float, float, float]
    uv: tuple[float, float] | None
    color: tuple[int, int, int] | None
    normal: tuple[float, float, float] | None = None


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _s10(v: int) -> int:
    v &= 0x3FF
    return v - 0x400 if v & 0x200 else v


def decode_vertices(data: bytes) -> list[Vertex]:
    """Every vertex the packed stream ``data`` submits, in order."""
    words = struct.unpack_from(f"<{len(data) // 4}I", data)
    n = len(words)
    i = 0
    pos = [0, 0, 0]  # 4.12 fixed, as ints, so VTX_XY etc. keep the rest
    uv: tuple[float, float] | None = None
    color: tuple[int, int, int] | None = None
    normal: tuple[float, float, float] | None = None
    out: list[Vertex] = []

    def emit() -> None:
        out.append(
            Vertex(
                (pos[0] / 4096, pos[1] / 4096, pos[2] / 4096),
                uv,
                color,
                normal,
            )
        )

    while i < n:
        packed = words[i]
        i += 1
        for shift in (0, 8, 16, 24):
            cmd = (packed >> shift) & 0xFF
            if cmd not in PARAM_COUNTS:
                raise DisplayListError(f"unknown geometry command {cmd:#04x}")
            count = PARAM_COUNTS[cmd]
            if i + count > n:
                raise DisplayListError("display list ends inside a command")
            params = words[i : i + count]
            i += count
            if cmd == 0x20:
                c = params[0]
                color = (c & 0x1F, (c >> 5) & 0x1F, (c >> 10) & 0x1F)
            elif cmd == 0x21:
                p = params[0]
                normal = (_s10(p) / 512, _s10(p >> 10) / 512, _s10(p >> 20) / 512)
            elif cmd == 0x22:
                p = params[0]
                uv = (_s16(p & 0xFFFF) / 16, _s16(p >> 16) / 16)
            elif cmd == 0x23:
                pos[0] = _s16(params[0] & 0xFFFF)
                pos[1] = _s16(params[0] >> 16)
                pos[2] = _s16(params[1] & 0xFFFF)
                emit()
            elif cmd == 0x24:
                p = params[0]
                pos[0] = _s10(p) << 6
                pos[1] = _s10(p >> 10) << 6
                pos[2] = _s10(p >> 20) << 6
                emit()
            elif cmd == 0x25:
                pos[0] = _s16(params[0] & 0xFFFF)
                pos[1] = _s16(params[0] >> 16)
                emit()
            elif cmd == 0x26:
                pos[0] = _s16(params[0] & 0xFFFF)
                pos[2] = _s16(params[0] >> 16)
                emit()
            elif cmd == 0x27:
                pos[1] = _s16(params[0] & 0xFFFF)
                pos[2] = _s16(params[0] >> 16)
                emit()
            elif cmd == 0x28:
                p = params[0]
                pos[0] += _s10(p)
                pos[1] += _s10(p >> 10)
                pos[2] += _s10(p >> 20)
                emit()
            # State commands a list could carry (0x29-0x2B, 0x40, 0x41) do not
            # change the vertices, so they are skipped once counted.
    return out


def color5_to_float(color: tuple[int, int, int]) -> tuple[float, float, float]:
    """5-bit RGB to 0..1."""
    return (color[0] / 31, color[1] / 31, color[2] / 31)
