# Geometry commands and fixed point

The DS geometry engine takes 32-bit words: one word packs up to four command
bytes, then each command's parameters follow in order. `DSE` models store
their vertices as such streams and the game DMAs them straight to the GPU, so
a display list in a file contains only per-vertex commands (`COLOR 0x20`,
`TEXCOORD 0x22`, `VTX_16 0x23`, occasionally the other `VTX_*` forms) — the
CPU issues `BEGIN_VTXS`, matrices, material and polygon state around them.
`app/dbkai/formats/gx.py` decodes the streams to vertices.

| Value | Format | Notes |
|---|---|---|
| vertex coordinate | s16, 4.12 | ±8 units; a model with larger extents is stored divided by 2ⁿ (the mesh's *shift*) and drawn behind `MTX_SCALE 2ⁿ` |
| texture coordinate | s16, 12.4 | in 1/16 texel; models use a 256-unit space — the game loads a texture matrix scaling by `width / 256`, so `u = s / 256` in 0..1 |
| colour | u16 RGB555 | 5 bits each, red lowest |
| matrix | 12 × s32, 20.12 | `MTX_LOAD_4x3` order: three rows of the 3×3, then the translation; applied to **row** vectors, `x' = x·m0 + y·m3 + z·m6 + m9` |

What the game sets around a character's display list (seen in `ds-probe`
traces and the ITCM draw routine at `0x01ffb7a8`):

- `POLYGON_ATTR`: modulate, alpha from the material, polygon id = state base
  + the mesh's group byte, back-face culling unless the mesh is double-sided,
  1-dot rendering, fog when the mesh's flag bit 5 is set.
- `TEXIMAGE_PARAM`: format and size from the texture, repeat/flip from the
  material, texgen mode 1 (texcoord matrix), colour-0 transparency when the
  texture's byte `0x26` bit 0 is clear.
- Per mesh: `MTX_MULT_4x3` with the bone's **world** matrix (vertices are
  bone-local), then `MTX_SCALE 2ⁿ` if the mesh has a shift.
- Meshes with flag bit 8 are preceded by a `BOX_TEST` on the 12 bytes stored
  in front of the mesh chunk, and skipped when off screen.

Vertex colours: a mesh whose list carries no `COLOR` command is drawn in the
colour word of its mesh-table entry (usually `0x7BDF`, near white).
