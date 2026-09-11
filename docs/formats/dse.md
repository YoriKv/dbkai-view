# `DSE` files

The game's one container for 3D data: character and prop models, single
motions, motion sets, and stage texture sets all carry the `DSE\0` magic.
`app/dbkai/formats/dse.py` parses them; `app/dbkai/model/` turns a model into
something drawable. Little-endian, offsets in bytes.

The layout was worked out from the uncompressed `/debug` files, every model
in the archive, `ds-probe` traces of what the game sends to the GPU, and the
ITCM draw routines (`0x01ffb7a8`, `0x01ffb494`, `0x01ffb2f0`, `0x01ffb038`,
`0x01ffad78`, `0x01ffbb48` in the [disassembly](../tools/ds-decomp.md)).

## Header (0x64 bytes)

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 4 | `DSE\0` |
| `0x04` | 2 | `"30"`, format version |
| `0x06` | 1 | kind bits: `0x01` model, `0x02` motion frames, `0x08` stage textures, `0x10` motion set, `0x20` unknown; `0x40` is set in RAM once the game has patched the file |
| `0x07` | 1 | `0x64` |
| `0x08` | 2 | exporter revision (1, 4 or 5) |
| `0x0A` | 20 | build id string, `YMMDDHHMMSS` of the export |
| `0x1E` | 2 | flag: 1 when the frame count below is exact (see *Frames*) |
| `0x20` | 2 | frame count |
| `0x22` | 2 | bone count |
| `0x24` | 2 | mesh count |
| `0x26` | 2 | display list count (the game iterates this many mesh-table rows) |
| `0x28` | 2 | material count |
| `0x2A` | 2 | texture count |
| `0x2C` | 2 | unused |
| `0x2E` | 2 | animation count |
| `0x30` | 13 × 4 | section table |

Section table, `T[0..12]`. "rel" offsets are relative to `0x64`; "abs" are
file offsets. `R(n) = 0x64 + T[n]`.

| Entry | Meaning |
|---|---|
| `T[0]` | size of the bone matrices; the matrices start at `0x64` and the bone records at `R(0)` |
| `T[1]` | rel: mesh table A (16 bytes per mesh) |
| `T[2]` | rel: mesh table B (16 bytes per mesh) |
| `T[3]` | rel: materials (36 bytes each) |
| `T[4]` | rel: textures (48 bytes each) |
| `T[5]` | rel: end of the texture table |
| `T[6]` | rel: animation table (12 bytes each), or 0 |
| `T[7]` | rel: string table |
| `T[8]` | string table size |
| `T[9]` | offset of the file's own name in the string table |
| `T[10]` | abs: pose frames, or 0 |
| `T[11]` | abs: texel and palette data |
| `T[12]` | abs: file size |

Between the bone records and `R(1)` lies the chunk stream with the display
lists. Every string reference below is an offset into the string table.

## Bones

`0x64` holds one 48-byte matrix per bone: a DS 4×3 matrix ([gx.md](gx.md)),
twelve 20.12 words. It is the **inverse bind** matrix — it takes a
model-space point into the bone's space, and its translation is minus the
bone's rest position. Bone records, 20 bytes each at `R(0)`:

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 1 | 1, or 2 for accessory bones (armour, mantle) |
| `0x01` | 1 | flags: bit 1 usually set; bit 2 = billboard bone (`BL_*`, drawn facing the camera); bit 0 is set in RAM for bones a skinned list refers to |
| `0x02` | 2 | 16-bit hash of the name. Not unique: `c_waist_geo1` and `r_elbow_geo1` collide |
| `0x04` | 2 | 0 |
| `0x06` | 2 | 1, or the frame count in motion files |
| `0x08` | 2 | name |
| `0x0A` | 2 | 0 |
| `0x0C` | 2 | parent index, -1 for a root |
| `0x0E` | 2 | mirror bone: the index used instead when the character is drawn mirrored (`l_hand` ↔ `r_hand`; self for centre bones) |
| `0x10` | 4 | rel offset of the bone's matrix |

Parents always precede children. Character skeletons are
`mute → offset → root → c_waist → c_chest → c_neck → c_head → hair…`, with
collars, shoulders, elbows and hands under the chest and hips, legs, knees
and feet under `root`; accessory bones (`mantle_s1`, `hair_skin1`, `skart_s`)
are extra roots at the origin.

The inverse bind matrix is not always a pure rotation and translation: a few
props and stage pieces bind a bone with a scale
(`nt_540003_bardock_finalspirits_planet` at 10×) or a mirror
(`500000_ground`, a negative x scale). The bind pose and the glTF export keep
it.

## Meshes

Mesh table A (16 bytes per mesh, at `R(1)`):

| Offset | Contents |
|---|---|
| `0x00` | name (`…Shape`) |
| `0x04` | rel offset of the mesh's chunk (see *Chunk stream*) |
| `0x08` | flags in the low 24 bits, **group** in the top byte |
| `0x0C` | colour: RGB555 in the low 16 bits, **shift** in bits 16-23 |

Flags: bit 0 = the lists carry `COLOR` commands; bit 2 = drawn twice, back
faces then front; bit 3 = cull mode none (both faces); bit 4 = front faces
culled instead of back, so the mesh is wound the other way (the inner side
of the Super Saiyan hair pieces); bit 5 = fog applies; bit 6 = always set;
bit 8 = run a `BOX_TEST` (parameters in the 12 bytes before the chunk)
before drawing.

The draw routine (`0x01ffb494`, per chunk, and `0x01ffb2f0`, which writes
`POLYGON_ATTR`) composes the attribute from these flags, the **alpha byte
of the material record** (`0x06` below, never the chunk's field), the
polygon id (the caller's base plus the mesh's group, capped at 63; a
separate id when the alpha is below 31) and the fog bit. Across the ROM 388
lists say alpha 0 in their chunk while their material says 31 (the cap
`200205_cap6`), and 83 say 31 while the material is translucent (the
scouter glass `toumei`, car windows, the death ball): the material is what
the game shows.

The **group** is a 0-15 slot the game switches with a 16-bit mask: 0 is the
body, 1 the head, 2-5 hair, arms and legs, 6-13 hand poses (`gu` fist, `pa`
open, `nigiri` grip, `kamehameha`, `sumsup`; even numbers right, odd left,
each with its matching `ristband_ID<n>`), 14 an alternative hairstyle. The
game also adds the group to the polygon id. The **shift** is how many bits
the vertices were shifted right to fit 4.12: the game multiplies by 2^shift
when drawing. The colour is what uncoloured meshes are drawn in.

Mesh table B (16 bytes per mesh, at `R(2)`), which the game iterates in
order: `name`, `bone index`, `index into table A`, 0.

### Chunk stream

A mesh's data is a run of chunks, each `u8 type, u8 flags, u16 value, u32
length` (length includes the header):

- **type 2**, length 8: selects a material: the low byte of `value` is the
  material index (the draw routine reads only that byte; the `alpha << 10`
  the tool wrote above it is never used, and is 0 on 388 lists whose
  material is opaque). A mesh begins with one and may contain more —
  Piccolo's knee mesh draws the ankle skin, then switches to the trouser
  material for the rest of the leg. `flags` bit 0 is set on a few meshes
  (meaning unknown).
- **type 3** (triangles) and **type 4** (quads): a display list. The 32-byte
  header continues `u16 vertex count, u16 layout, u32 weights offset, u16
  bones[…]`; the vertex data follows to `length`. `layout`'s low byte is the
  bytes per vertex; its high byte: `0x20` = packed GX commands the game DMAs
  ([gx.md](gx.md)); `0x10` = a raw vertex array the game skins on the CPU;
  `0x08` = has texture coordinates; `0x04` = has colours; `0x02` = has
  normals.
- **type 1**, length 8: ends the mesh. Not always present — a mesh also ends
  at the next mesh's table offset. A table-A entry that points at a type-1
  chunk is an empty mesh.

A **packed** list's vertices are in the mesh's bone's local space: the game
draws the list behind that bone's world matrix. Texture coordinates are in a
256-unit space (`u = s / 256`).

A **raw (CPU-skinned)** list's vertices are in model space (bind pose), each
`s16 s, t` (12.4), optional `u16 RGB555 colour`, `s16 x, y, z` (4.12), then
one `u16` 4.12 weight per bone in the header's list, starting at the header's
*weights offset* (10, or 12 with colour). The game transforms each vertex by
the weighted sum of `world × inverse bind` of those bones and writes it to
the GPU itself. Up to seven bones per list have been seen.

Quads are stored as such and triangulated by the reader.

## Materials (36 bytes each, at `R(3)`)

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 4 | name |
| `0x04` | 2 | bit 0 = textured; bits 2-3 = repeat S, T; bits 8-9 = flip S, T (flip implies repeat); bit 7 set in RAM to hide |
| `0x06` | 1 | alpha, 0-31: what the draw routine puts in `POLYGON_ATTR` (31 opaque; below 31 the mesh is drawn translucent with the translucent polygon id) |
| `0x07` | 1 | **part** id, 0-15 |
| `0x08` | 2 | diffuse RGB555 |
| `0x0A` | 4 | 0 |
| `0x0E` | 2 | `0x1CE7` |
| `0x10` | 1 | texture index, `0xFD` for none |
| `0x11` | 1 | `0xFD` |
| `0x12` | 2 | two `s8`: direction of the S and T texture scroll (negative runs backwards) |
| `0x14` | 4 | period in frames of the S scroll (1 = none) |
| `0x18` | 4 | period of the T scroll |
| `0x1C` | 4 | phase of the S scroll, `0x20` of the T scroll |

The texture setup (`0x01ffad78`) writes `TEXIMAGE_PARAM` from the texture's
size, format and colour-0 bit, the material's repeat and flip bits (flip
OR-ed into repeat, as the hardware needs), and the texture matrix from the
scroll: with a period above 1, the texture offset is `((time + phase) mod
period) / period` of the texture size, in the direction given, so the sky
of the Kinto'un stage and the fast backgrounds of the arenas move. 165
materials scroll; the viewer and the export do not animate them yet.

The palette a material draws with is chosen by the draw state's colour
scheme, but not as palette *n* of the texture directly: the routine counts
the schemes flagged in a per-model word below the wanted one and takes that
many palettes in. Where that word is filled has not been read; the viewer
takes palette *n*.

The **part** is the second 16-bit visibility mask: 0 always, 1-4 the faces
(`F_01_nomal`, `F_02_damage`, `F_03_kusen`, `F_04_fun`), 5-8 the mouths
(`M_05`…`M_08`), 11-13 lip-sync mouths, 14 sweat, 9 and 15 alternative hair
materials. A mesh is drawn only when both its group bit and its material's
part bit are set. The masks come from the character's action sets
([dsa.md](dsa.md)); on the title screen Goku shows parts 0, 1, 8 and 15 with
groups 0-5, 8 and 9.

## Textures (48 bytes each, at `R(4)`)

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 4 | source path (`K:/design/DB_data/chr/goku/tex/goku_03.tm2`) |
| `0x04` | 4 | texel data offset, relative to `T[11]` |
| `0x08` | 4 | unpacked texel size |
| `0x0C` | 4 | packed size (not reliable in every file) |
| `0x10` | 4 | palette offset, relative to `T[11]` |
| `0x14` | 4 | palette size in bytes, all palettes together |
| `0x18` | 2 | width |
| `0x1A` | 2 | height |
| `0x1C` | 8 | 0 in the file; VRAM addresses once loaded |
| `0x24` | 1 | DS texture format (1 A3I5, 2 pal4, 3 pal16, 4 pal256, 6 A5I3) |
| `0x25` | 1 | colours per palette (0 = 256) |
| `0x26` | 1 | bit 0 clear = colour 0 is transparent |
| `0x27` | 1 | palette count (the game's alternate colour schemes) |
| `0x28` | 4 | file name |
| `0x2C` | 4 | varies |

Texel data is raw, [LZ77 or LZSS](compression.md); a block's first bytes
say which. Palettes are RGB555. `nt_` models list textures but ship no data.

## Animation table (12 bytes each, at `R(6)`) and frames

`name, u16 start frame, u16 id, u16 first, u16 last`. A clip runs `last -
first + 1` frames from `start` in the frame array. `first` and `last` are
1-based frame numbers of the source take, which is how a run/attack split
into start, loop and end clips is recorded.

Frames sit at `T[10]`, one 12-byte pose per bone per frame, bones in file
order. The array holds header frame count + 1 frames (the flag at `0x1E` is
1 when it is exact). A model file carries one frame: its rest pose. Pose:

| Bits | Contents |
|---|---|
| word 0 `[31:20]`, `[19:8]` | quaternion y, x — signed 12-bit, `0x800` = ±1.0 |
| word 0 `[7:0]` | high byte of the z translation |
| word 1 `[31:20]`, `[19:8]` | quaternion w, z |
| word 1 `[7:0]` | low byte of the z translation |
| word 2 `[31:16]`, `[15:0]` | y, x translation, s16 7.9 |

The pose is the bone's transform relative to its parent (`T · R`). World
matrices follow by forward kinematics; `world × inverse bind` is the skin
matrix. Frame 5772 of `sm_100000_NORMAL.dse` (the `e_idle_ground` clip)
reproduces the matrices the game sends for Goku on the title screen to within
fixed-point rounding.

Motion sets (`smot/sm_<body type>_*.dse`) are shared by every character of a
body type; a model's bones are matched to the set's by name. The body type
is the model id rounded down to 10000 (`101100_goku` → `100000 NORMAL`).
Character bones absent from the set keep their bind pose.
