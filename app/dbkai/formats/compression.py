"""The three compressions the game uses, as pure functions on bytes.

- :func:`lz77_decompress`: the DS BIOS's LZ77 (type ``0x10``). Wraps every
  entry of ``archiveDBK.dsa`` that is not stored raw, and the ``.dse7`` files.
- :func:`lzss_decompress`: Okumura LZSS, which wraps the texel data embedded in
  ``DSE`` models.
- :func:`lzma_alone_decompress`: LZMA "alone" streams, the ``.dsez`` and
  ``.dsdz`` files.

Details of each stream layout are in ``docs/formats/compression.md``.
"""

from __future__ import annotations

import lzma
import struct


class CompressionError(ValueError):
    """The stream is not what the decoder was told it is."""


def lz77_decompress(src: bytes) -> bytes:
    """Unpack a BIOS LZ77 stream: a 4-byte header (type ``0x10`` in the low
    byte, the unpacked size in the upper 24 bits) and then flag-prefixed
    groups of eight tokens, MSB first, where a set flag is a two-byte
    back-reference of ``(length - 3) << 12 | (distance - 1)``."""
    if len(src) < 4:
        raise CompressionError("LZ77 stream shorter than its header")
    header = struct.unpack_from("<I", src)[0]
    if header & 0xFF != 0x10:
        raise CompressionError(f"not a BIOS LZ77 stream (type {header & 0xFF:#x})")
    size = header >> 8
    out = bytearray()
    i = 4
    n = len(src)
    while len(out) < size:
        if i >= n:
            raise CompressionError("LZ77 stream ended early")
        flags = src[i]
        i += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                if i + 2 > n:
                    raise CompressionError("LZ77 stream ended inside a token")
                a, b = src[i], src[i + 1]
                i += 2
                length = (a >> 4) + 3
                distance = ((a & 0x0F) << 8 | b) + 1
                if distance > len(out):
                    raise CompressionError("LZ77 back-reference before the start")
                for _ in range(length):
                    out.append(out[-distance])
            else:
                if i >= n:
                    raise CompressionError("LZ77 stream ended inside a literal")
                out.append(src[i])
                i += 1
    return bytes(out[:size])


# Okumura's constants: a 4 KiB ring, 18-byte maximum match, 2-byte threshold.
_LZSS_RING = 4096
_LZSS_MAX_MATCH = 18


def lzss_decompress(src: bytes, size: int) -> bytes:
    """Unpack ``size`` bytes of Okumura LZSS.

    Flag bytes are read LSB first and a **set** bit means a literal. A clear
    bit is a two-byte reference ``lo, hi``: ring position ``lo | (hi & 0xF0)
    << 4`` and length ``(hi & 0x0F) + 3``. The ring starts zero-filled with
    the write cursor at ``4096 - 18``, which is what lets the very first
    reference copy zeros from "before" the output.
    """
    ring = bytearray(_LZSS_RING)
    r = _LZSS_RING - _LZSS_MAX_MATCH
    out = bytearray()
    i = 0
    n = len(src)
    flags = 0
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            if i >= n:
                raise CompressionError("LZSS stream ended early")
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            if i >= n:
                raise CompressionError("LZSS stream ended inside a literal")
            c = src[i]
            i += 1
            out.append(c)
            ring[r] = c
            r = (r + 1) & (_LZSS_RING - 1)
        else:
            if i + 2 > n:
                raise CompressionError("LZSS stream ended inside a reference")
            lo, hi = src[i], src[i + 1]
            i += 2
            pos = lo | ((hi & 0xF0) << 4)
            length = (hi & 0x0F) + 3
            for k in range(length):
                c = ring[(pos + k) & (_LZSS_RING - 1)]
                out.append(c)
                ring[r] = c
                r = (r + 1) & (_LZSS_RING - 1)
    return bytes(out[:size])


def lzma_alone_decompress(src: bytes) -> bytes:
    """Unpack an LZMA "alone" stream (``.dsez``, ``.dsdz``)."""
    try:
        return lzma.decompress(src, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError as exc:
        raise CompressionError(f"bad LZMA stream: {exc}") from exc


def is_lz77(src: bytes) -> bool:
    """Whether ``src`` starts like a BIOS LZ77 stream."""
    return len(src) >= 4 and src[0] == 0x10


def is_lzma_alone(src: bytes) -> bool:
    """Whether ``src`` starts like the LZMA "alone" streams the game ships:
    properties byte ``0x5D`` (lc=3, lp=0, pb=2) and a power-of-two
    dictionary size."""
    if len(src) < 13 or src[0] != 0x5D:
        return False
    dictionary = struct.unpack_from("<I", src, 1)[0]
    return dictionary >= 4096 and dictionary & (dictionary - 1) == 0
