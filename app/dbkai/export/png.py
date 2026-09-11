"""A minimal PNG encoder for RGBA8 pixels, so the extractor needs no imaging
library."""

from __future__ import annotations

import struct
import zlib


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """``rgba`` is ``width * height * 4`` bytes, top row first."""
    if len(rgba) != width * height * 4:
        raise ValueError("pixel buffer does not match the size")
    stride = width * 4
    raw = b"".join(b"\0" + rgba[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
