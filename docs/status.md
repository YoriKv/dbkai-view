# State of the project

What DBKai reads, shows and writes today, and what is still open. The detail
is in [`formats/`](formats/) and [`app/viewer.md`](app/viewer.md).

## Decoded

- **The cartridge**: header, NitroFS, code modules and overlays
  ([formats/rom.md](formats/rom.md)); `archiveDBK.dsa` with its 3036 entries
  ([formats/archive.md](formats/archive.md)); BIOS LZ77, Okumura LZSS and
  LZMA-alone ([formats/compression.md](formats/compression.md)).
- **`DSE` containers** ([formats/dse.md](formats/dse.md)): bones with
  inverse-bind matrices, meshes as GX display lists (bone-local, packed) or
  CPU-skinned raw vertices with weights, materials, textures in every DS
  format with multiple palettes, pose frames as 12-byte quaternion records,
  motion sets with named clips. Forward kinematics matches the matrices the
  game uploads, checked against an emulator trace.
- **Draw semantics**: the 32-bit visibility mask (mesh groups low, material
  parts high), culling and alpha per mesh, billboard bones, the mesh scale
  shift.
- **Action files** ([formats/dsa.md](formats/dsa.md)): actions and their
  command chains; motion (`0x11`), visibility (`0x12`), colour scheme
  (`0x13`), link (`0x03`) and sound (`0x09`) commands; resources resolved to
  motion-set clips by number; the `PRM` parameter table with the visibility
  presets the game applies by state.

## The viewer and the extractor

- Browses every model, motion set and texture set, including the ones inside
  the story packages; draws with the game's rules; poses from the body's
  motion set; plays actions with their clips and part switches.
- Exports glTF 2.0 (`.glb`, unlit materials, skin, 60 fps samplers) as one
  file, one per clip, or the whole ROM at once; textures as PNG.
- Headless: `python -m dbkai.cli list | extract | export`.

## Open

- Action ops `0x01`, `0x04`, `0x14` and the rest, the 12-byte header
  entries, root-motion records and resource flags
  ([formats/dsa.md](formats/dsa.md#not-read-yet)).
- `DSE` kind bit `0x20`, and material chunk flag bit 0.
- The 121 stage texture sets in `st/` decode, but where the stage geometry
  lives has not been located.
- Effects the ultimates spawn (embedded resources) are not played with the
  action.
