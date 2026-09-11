# ROM layout

What the cartridge image contains, as reported by
[`dsd rom extract` and `dsd init`](../tools/ds-decomp.md) on the Japanese
release. Addresses are ARM9 memory addresses.

## Header

| | |
|---|---|
| Title / game code / maker | `DBK ULTIMATE` / `TDBJ` / `AF` (Namco Bandai) |
| Size | 64 MiB |
| Secure area | decrypted in the dump |
| DS Protect | present, on overlay 0 only (version 2.01 Instant) |

## Code modules

| Module | Load address | Code | Notes |
|---|---|---|---|
| ARM9 | `0x02000000`, entry `0x02000800` | `0x0dd700` bytes, compressed in the ROM | `.bss` runs to `0x021282c0` |
| ITCM | `0x01ff8000` | `0x7780` bytes | autoload |
| DTCM | `0x027e0000` | `0x140` bytes | autoload |
| ARM7 | `0x02380000` | `0x26488` bytes | not analysed by `dsd` |

ARM9 sections, from `delinks.txt`:

| Section | Range |
|---|---|
| `.text` | `0x02000000`–`0x020d010c` |
| `.exception` / `.exceptix` | `0x020d010c`–`0x020d0300` |
| `.init` | `0x020d0300`–`0x020d2ef8` |
| `.rodata` | `0x020d2ef8`–`0x020d8968` |
| `.ctor` | `0x020d8968`–`0x020d8a5c` |
| `.data` | `0x020d8a60`–`0x020dd700` |
| `.bss` | `0x020dd700`–`0x021282c0` |

`dsd init` finds 4178 functions in the ARM9 binary and 24 096 relocations.
The `.exception`/`.exceptix` tables and the `.ctor` list are the CodeWarrior
C++ layout that `dsd` targets, so the game is inferred to be C++ built with
the Nintendo SDK's CodeWarrior toolchain.

## Overlays

110 ARM9 overlays, all compressed in the ROM, loaded into three fixed slots directly
after the ARM9 `.bss`:

| Slot | Overlays | Largest | Role |
|---|---|---|---|
| `0x021282c0` | 0–7 | `0x177c0` bytes | large modules; 0 is the DS Protect one |
| `0x0213fce0` | 8–58 | `0x17c0` bytes | small modules, one resident at a time |
| `0x021414a0` | 59–109 | `0x17c0` bytes | small modules, one resident at a time |

Two overlays can be resident together only if they come from different slots.
Which slot-2 and slot-3 overlays pair with which game mode is not worked out
yet.

## File system

1068 files. The top level:

| Path | Size | Contents |
|---|---|---|
| `archiveDBK.dsa` | 16 MiB | the main archive — models, 2D resources, stages, effects (`reference/archive/`) |
| `smot/` | 23 MiB | `sm_*.dse` |
| `sound/` | 14 MiB | |
| `sp/` | 6.6 MiB | `.dsdz` (LZMA) |
| `dsa/` | 3.5 MiB | 90 per-character `.dsa` archives, named `<id>_<character>[_sN]_ultimate.dsa` |
| `debug/` | 1.4 MiB | test primitives (`cube_100B.dse`, `sphere_R50.dse`, …) and `MakeDsa.inf` |
| `gamedata/` | 1.1 MiB | `ai/`, `challenge/`, `episode/`, `system/` |
| `font/`, `msg/`, `scene/`, `eff/` | < 350 KiB each | `scene/scn*.bin` |

`reference/fs/` and `reference/dsd/rom/files/` are the same tree.
