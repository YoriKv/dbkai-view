# `archiveDBK.dsa`

The 16 MiB pack holding most assets. `app/dbkai/formats/archive.py` reads it.
Little-endian throughout.

## Header

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 4 | `'DSA '` |
| `0x04` | 4 | version, `1` |
| `0x08` | 4 | offset of the first entry's data (the size of everything before it) |
| `0x0C` | 4 | directory count, `10` |
| `0x10` | 12 × count | directory table |
| then | 20 × entries | entry table |
| then | | directory name strings, then more names (unused here) |

A directory is `(first entry index, entry count, name offset)`; the name is a
NUL-terminated path such as `/mdl/chr`. The entry table ends where the first
directory name begins, which gives the entry count (3036).

## Entries

Each 20-byte entry is `(name length, id, unpacked size, packed size,
offset)`. At `offset` sit `name length` bytes of NUL-padded file name,
followed by the data. When the packed and unpacked sizes differ the data is a
[BIOS LZ77 stream](compression.md); otherwise it is stored raw. Entries in
`/gamedata/*` have a name length of 0 and are named after their id.

The id is the number the file name starts with; an `nt_` variant of a model
has the same id with bit 31 set.

## Directories

| Directory | Count | Contents |
|---|---|---|
| `/eff` | 1450 | `.dsf` effects |
| `/gamedata/accessory`, `attack`, `chain`, `parameter` | 189 | `PRM` tables |
| `/mdl/chr` | 585 | `.dse` character, prop and cutscene models (see [dse.md](dse.md)) |
| `/res2D/bg`, `/res2D/obj` | 690 | `.dsb` backgrounds and `.dso` sprites |
| `/sp/common` | 1 | one model |
| `/st` | 121 | `st_*.dse`, stage texture sets |

An `nt_<id>_<name>.dse` model is the same model as `<id>_<name>.dse` but
without its texel data; its texture table still lists the textures, so the
viewer takes them from the sibling.
