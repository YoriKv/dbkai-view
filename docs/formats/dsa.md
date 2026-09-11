# `DSA` action files

`dsa/<id>_<name>.dsa`: what a character does, frame by frame — one file per
body type and fighting style (`101000_NORMAL_BALANCE`, `111000_TALL_POWER`,
…), a few character overrides numbered right after one (`101001_GOKU`), and
one `<model id>_<name>_ultimate` per character for its ultimate attack.
`app/dbkai/formats/dsa.py` parses them; the layout comes from the game's
loader (`0x02075d2c`) and the interpreters (`0x0207cbb8`, `0x02076a08`).
Little-endian. The 16.7 MiB `archiveDBK.dsa` is a different format
([archive.md](archive.md)).

## Header (0x24 bytes)

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 4 | `DSA\0` |
| `0x04` | 2 | version `01 11` |
| `0x06` | 2 | header size, `0x24` |
| `0x08` | 2 | action count *A* |
| `0x0A` | 2 | record count *R* (actions included) |
| `0x0C` | 2 | resource count |
| `0x0E` | 2 | count of 12-byte entries (unknown) |
| `0x10` | 2 | motion set count |
| `0x12` | 2 | 0 |
| `0x14` | 4 | offset of the command area |
| `0x18` | 4 | offset of the embedded-data area, relative to the command area |
| `0x1C` | 4 | 0 |
| `0x20` | 4 | `0x400` |

Then, back to back: *R* u32 record offsets (relative to the command area;
the first *A* are the actions), the resource table (20 bytes each), the
12-byte entries, and the motion set ids (u32 each, e.g. `100000` for
`sm_100000_NORMAL`).

## Records

Every record starts with the same 16 bytes:

| Offset | Contents |
|---|---|
| `0x00` | opcode |
| `0x01` | flags: bit 0 active, bit 7 conditional on the byte at `0x02` matching a character state |
| `0x02` | condition value |
| `0x03` | `0xFF` |
| `0x04` | u16 `3` |
| `0x06` | s16 start frame |
| `0x08` | s16 duration in frames, 0 = to the end of the action |
| `0x0A` | s16 -1 |
| `0x0C` | s16 -1 |
| `0x0E` | s16 index of the next record in the chain, 0 = end |

An **action** is a record of opcode 0 whose duration is the action's length
and whose u32 at `0x14` is its id (1000, 1100, 2000, … 19120): the ids the
game's other tables (`gamedata/attack`, `gamedata/accessory`) refer to. Its
chain links the action's commands.

Commands seen (opcode: meaning, where the handler has been read):

| Op | Meaning |
|---|---|
| `0x03` | link: at `start` the character may branch to the action whose record index is the s16 at `0x16` |
| `0x04`, `0x14` | hit boxes and the group that turns them on |
| `0x09` | play a resource-table entry (s16 index at `0x10`) — sounds and effects, keyed like motion clips |
| `0x11` | attack parameters |
| `0x12` | **visibility**: `0x14` = mode; mode 1 uses the u32 at `0x18` as the draw mask, mode 2 evaluates two tracks at the offsets in `0x1C` (groups) and `0x20` (parts), relative to the command area, at the frame since `start` |
| `0x13` | **colour scheme**: the byte at `0x14` picks the texture palette |
| others | not read |

The draw mask is the word the draw routine tests
([dse.md](dse.md#materials-36-bytes-each-at-r3)): mesh groups in the low 16
bits, material parts in the high 16. `0x8023033F` — groups 0-5, 8, 9; parts
0, 1, 5, 15 — is the rest look (open hands, neutral face); `0x804300FF`
swaps in fists and the damage mouth. Actions without a visibility command
leave the mask as the previous action set it.

A **track** is `u8 flags, u8 count, s16 period, count × u16 values, count ×
keys` (u8 keys when flags bit 0 is set, else u16); flags bit 1 loops the
frame over `period`. The value is the last key at or before the frame,
`0xFFFF` before the first.

## Resources

Twenty bytes: `u16 -1, u16 -1, s32 number, u32 set id, s32 offset, u32
flags`. A motion-set clip has the set id and the clip's leading number
(`10` → `00010_100000_NORMAL_idle_ground`); an embedded resource has set 0,
a negative number, and its data at `offset` into the command area. What
plays a motion for an action has not been found: the idle actions carry no
motion command, so the game selects motions elsewhere.

## Visibility presets

`archiveDBK.dsa` entry 64419 (`/gamedata/parameter`, a `PRM` table with
16-byte records `u32 state id, u32 mask, -1, -1`) lists the draw masks the
game gives a character by state. Ids 10000-10006 are the neutral face
(parts 0, 1, 5, 15) with the seven hand pairings — 10000 both fists,
10005 both open, 10006 both gripping; 11000-11006 the same with the damage
mouth (part 6), 12000-12002 with the damage face (part 2); 20000-21006
repeat the pairings with the grip hands 10 and 11; 19000 hides everything.
The viewer rests on preset 10005, the look the title and character-select
screens also draw (their own masks, `0x8123033F` in the title code and
`0x8023F33F` at `0x0213b680` in overlay 2, share its face and open hands).

`PRM` tables (`app/dbkai/formats/prm.py`): `PRM\0`, u16 `0x7755`, u16
`0x1000`, four kind bytes, `-1`, u16 record count at `0x12`, u16 header size
at `0x14`, u16 record size at `0x16`, then the records.
