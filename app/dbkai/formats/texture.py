"""DS texture formats decoded to RGBA8 pixels.

``decode`` takes the raw texel bytes, the palette (as RGB555 words) and the
TEXIMAGE_PARAM format number, and returns one ``bytes`` of ``width * height *
4`` RGBA. Colour 0 of a paletted format is transparent when ``color0_transparent``
is set, as on the hardware.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum


class TextureFormat(IntEnum):
    NONE = 0
    A3I5 = 1
    PAL4 = 2
    PAL16 = 3
    PAL256 = 4
    TEX4X4 = 5
    A5I3 = 6
    DIRECT = 7

    @property
    def bits_per_texel(self) -> int:
        return {0: 0, 1: 8, 2: 2, 3: 4, 4: 8, 5: 2, 6: 8, 7: 16}[self.value]

    @property
    def palette_colors(self) -> int:
        """Colours one palette of this format spans (0 for direct colour)."""
        return {0: 0, 1: 32, 2: 4, 3: 16, 4: 256, 5: 0, 6: 8, 7: 0}[self.value]


class TextureError(ValueError):
    """The texels cannot be decoded as the format and size given."""


@dataclass(frozen=True)
class Rgba:
    width: int
    height: int
    pixels: bytes  # RGBA8, row-major, top row first

    def __post_init__(self) -> None:
        if len(self.pixels) != self.width * self.height * 4:
            raise TextureError("pixel buffer does not match the size")


def rgb555_to_rgb8(c: int) -> tuple[int, int, int]:
    return (
        (c & 0x1F) * 255 // 31,
        ((c >> 5) & 0x1F) * 255 // 31,
        ((c >> 10) & 0x1F) * 255 // 31,
    )


def palette_words(palette: bytes) -> list[int]:
    return list(struct.unpack_from(f"<{len(palette) // 2}H", palette))


def decode(
    fmt: TextureFormat | int,
    width: int,
    height: int,
    texels: bytes,
    palette: bytes = b"",
    color0_transparent: bool = False,
) -> Rgba:
    fmt = TextureFormat(fmt)
    count = width * height
    if fmt == TextureFormat.TEX4X4:
        raise TextureError("4x4 compressed textures are not supported")
    needed = count * fmt.bits_per_texel // 8
    if len(texels) < needed:
        raise TextureError(
            f"{fmt.name} {width}x{height} needs {needed} bytes, got {len(texels)}"
        )
    pal = palette_words(palette)

    def color(index: int) -> tuple[int, int, int]:
        return rgb555_to_rgb8(pal[index]) if index < len(pal) else (0, 0, 0)

    out = bytearray(count * 4)
    if fmt == TextureFormat.DIRECT:
        for i in range(count):
            c = texels[2 * i] | (texels[2 * i + 1] << 8)
            r, g, b = rgb555_to_rgb8(c)
            out[4 * i : 4 * i + 4] = (r, g, b, 255 if c & 0x8000 else 0)
        return Rgba(width, height, bytes(out))

    if fmt == TextureFormat.A3I5:
        for i in range(count):
            p = texels[i]
            r, g, b = color(p & 0x1F)
            a = ((p >> 3) & 0x1C) + (p >> 6)  # 3-bit alpha spread to 5
            out[4 * i : 4 * i + 4] = (r, g, b, a * 255 // 31)
        return Rgba(width, height, bytes(out))

    if fmt == TextureFormat.A5I3:
        for i in range(count):
            p = texels[i]
            r, g, b = color(p & 0x7)
            a = p >> 3
            out[4 * i : 4 * i + 4] = (r, g, b, a * 255 // 31)
        return Rgba(width, height, bytes(out))

    # Plain paletted formats, with colour 0 optionally transparent.
    alpha0 = 0 if color0_transparent else 255
    cache: dict[int, tuple[int, int, int, int]] = {}

    def rgba(index: int) -> tuple[int, int, int, int]:
        got = cache.get(index)
        if got is None:
            got = (*color(index), alpha0 if index == 0 else 255)
            cache[index] = got
        return got

    if fmt == TextureFormat.PAL256:
        for i in range(count):
            out[4 * i : 4 * i + 4] = rgba(texels[i])
    elif fmt == TextureFormat.PAL16:
        for i in range(count):
            p = texels[i >> 1]
            out[4 * i : 4 * i + 4] = rgba((p >> 4) if i & 1 else (p & 0xF))
    elif fmt == TextureFormat.PAL4:
        for i in range(count):
            p = texels[i >> 2]
            out[4 * i : 4 * i + 4] = rgba((p >> ((i & 3) * 2)) & 0x3)
    else:
        raise TextureError(f"cannot decode format {fmt.name}")
    return Rgba(width, height, bytes(out))
