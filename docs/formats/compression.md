# Compression

Three schemes wrap the game's data. All three are implemented in
`app/dbkai/formats/compression.py`.

## BIOS LZ77 (type `0x10`)

The DS BIOS format. Four-byte header: `0x10` in the low byte, the unpacked
size in the upper 24 bits. Then groups of eight tokens behind a flag byte
read MSB first: a clear bit is one literal byte, a set bit is two bytes
`(length - 3) << 12 | (distance - 1)` (big-endian nibbles: `a >> 4` is the
length, `(a & 15) << 8 | b` the distance).

Used by: every compressed entry of `archiveDBK.dsa`, `.dse7` files, and some
texel blocks inside `DSE` models.

## Okumura LZSS

The classic 4 KiB-ring LZSS: flag bytes read LSB first, a **set** bit is a
literal, a clear bit is a two-byte reference `lo, hi` with ring position
`lo | (hi & 0xF0) << 4` and length `(hi & 0x0F) + 3`. The ring starts
zero-filled with the write cursor at `4096 - 18`, so a reference near the
end of the ring at the start of a stream copies zeros.

Used by: most texel blocks inside `DSE` models, behind a 4-byte little-endian
unpacked size. There is no magic; a block is LZSS when its first word equals
the texture's unpacked size and BIOS LZ77 when its first byte is `0x10` and
`word >> 8` equals it. A block whose length already equals the unpacked size
is stored raw.

## LZMA "alone"

Standard `.lzma` streams (properties `0x5D`, 16 MiB dictionary, 64-bit size),
which Python's `lzma.FORMAT_ALONE` reads.

Used by: `.dsez` models and `.dsdz` packages in the NitroFS.
