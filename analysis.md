# Dragon Ball Kai: Ultimate Butou Den (Japan) — ROM analysis notes

ROM: `../Dragon Ball Kai - Ultimate Butou Den (Japan).nds`. How to use the tools is covered in
`../melonDS/CLAUDE.md`. Every address below was observed with `ds-probe`/GDB, running the
software renderer with the JIT off, using FreeBIOS, direct boot and no save file. Frame numbers
count from power-on with no input unless a press is stated.

## Header
| Field | Value |
|---|---|
| Title / game code | `DBK ULTIMATE` / `TDBJ` (cart ID `00003FC2`), 64 MiB |
| ARM9 | ROM offset `0x4000`, load and entry `0x02000000` / `0x02000800`, size `0x8DFF0` |
| ARM7 | ROM offset `0x100A00`, load and entry `0x02380000`, size `0x26488` |
| Overlays | 110 ARM9 overlays (OVT at ROM `0x92000`) |
| NitroFS | FNT at `0x127000`, FAT at `0x12B600`, 1178 FAT entries (1068 named files) |

## Boot timeline (no input)
| Frame | What's on screen | 3D |
|---|---|---|
| 1–720 | logos and black | none |
| ~738–1440 | intro cutscene: 3D Goku, dragon ball, clouds (screenshot at 840) | ~300–670 polygons |
| 1441 | the title-screen model is decompressed into `0x022CB6E4` (see below) | |
| ~1560+ | title screen: 2D logo on top; 3D Goku in front of 2D Capsule Corp on the bottom | 600+ polygons at frame 3180 |
| START at 1700, then idle to 2700 | save-data select (データ選択) with two empty slots (はじめから) | none |
| A at 2710 | name entry (セーブデータに名前をつけてください) | none |

Command to reproduce the save-select state (frame numbers restart at 1 after loading it):
`ds-probe ROM --frames 2700 --press 1700:START --save-state 2700:dataselect.mln`.
The flow after name entry (main menu → battle) has **not** been explored yet.

DISP3DCNT is `0x0039` during the intro and `0x01B9` on the title screen: texturing, alpha
blending, antialiasing, edge marking (title only) and fog. VRAMCNT A..G is
`83 8B 80 84 82 85 83`:
- A = texture slot 0
- B = texture slot 1
- D = texture slot 2
- E = texture palette
- C = ARM7 WRAM (MST 4 at offset 0)
- F and G = texture palette slots

## How the game submits geometry
- **Immediate-mode DMA0 bursts**, not GXFIFO-mode (mode 7) DMA, and very few direct CPU
  command-port writes. Title frame 3180: 20,517 FIFO words, of which 17,059 arrive in immediate
  DMA0 bursts of 5–89 words each.
- The kick routine is a generic DMA helper in **ITCM**: `0x01FFF444`, prologue
  `push {r3-r9,lr}`. It takes r0 = DMA channel, r1 = source, r2 = destination and r3 = control.
  It writes DMAxSAD/DAD/CNT at `0x01FFF4B0`/`B4`/`B8`, where `0x01FFF4B8` is `str r6,[r1,#8]`.
  The same helper also does OAM copies (dst `0x07000000`), so a breakpoint on it needs a
  condition on `r2`/`r8`.
- The CPU writes other state (POLYGON_ATTR, BEGIN_VTXS, MTX_*, TEXIMAGE_PARAM, PLTT_BASE)
  straight to the command ports from ITCM code around `0x01FFAE64`–`0x01FFBE08` and from main-RAM
  code around `0x020A2710`–`0x020C285C`. Example: `0x01FFB340 str r1,[r0]` with `r0=0x040004A4`
  writes POLYGON_ATTR.
- Command mix per title frame: ~4.8k VTX_16, ~4.7k TEXCOORD, ~4k COLOR, ~70 BEGIN_VTXS (all
  `triangles`), ~40 TEXIMAGE_PARAM/PLTT_BASE, ~100 matrix loads/multiplies, one
  SWAP_BUFFERS (auto-sort, Z-buffer) and one VIEWPORT. No NORMAL/lighting commands (lights=0);
  shading is baked into per-vertex COLOR.
- Polygon attributes seen: `modulate alpha=31 lights=0 cull-back 1dot fog`, polygon IDs 1–3.
  Textures are mostly pal256 128×128 (character atlases), plus pal16/a5i3 for effects and
  clouds. TEXIMAGE_PARAM sets repeat and flip on S and T with texgen mode 1 (texcoord matrix).

## Model data: the DSE format
- **Magic:** `DSE\0` models, alongside `DSA\0` animations, `DSF\0` effects and `DSD\0` special
  data. These are custom formats, not Nitro NSBMD, and no `BMD0`/`BTX0` appears anywhere in
  RAM.
- **Compression:**
  - `.dsez` / `.dsdz`: LZMA "alone" streams (props `0x5D`, dictionary `0x01000000`, 64-bit
    size). They unpack with Python `lzma.FORMAT_ALONE`.
  - `.dse7`: BIOS LZ77 (type `0x10`).
  - Checked: `/debug/goku/goku.dsez` and `goku.dse7` both unpack byte-for-byte to
    `/debug/goku/goku.dse` (107,048 bytes).
- **Header (partly understood):**

  | Offset | Contents |
  |---|---|
  | `0x00` | `DSE\0` |
  | `0x04` | `"30"` (version?) |
  | `0x06` | u8 flags / u16 |
  | `0x0A` | ASCII build or ID string (e.g. `01111133940`, `80516003101`) |
  | `0x22` | u16 counts (e.g. `1A 40 40 11 03`: meshes/bones?) |
  | `0x30` | table of 13 u32 offsets to sections, then `0x00001000` |

  - The **last offset equals the file size**.
  - In the title-screen model the first section starts at `0x4E0` and runs to `0x1DBE4`, and it
    holds the **raw packed GX display lists**: DMA0 sources map to file offsets
    `0x1878C`–`0x1D854`.
  - So display lists are DMA'd straight out of the loaded model file. Vertices are model-space
    VTX_16 values; the skeleton is applied through matrix commands, not by CPU skinning.
- **NitroFS layout (1068 files):**

  | Path | Contents |
  |---|---|
  | `/sp/*.dsdz` | 600 files, LZMA → `DSD\0` |
  | `/gamedata/*.bin` | 240 files: `PRM\0`, `SCR*`, `RATT`, … |
  | `/dsa/*.dsa` | 90 animations (`101001_GOKU.dsa`, …) |
  | `/smot/sm_*_{NORMAL,TALL,BOY,BABY,MUSCLE,WOMAN,KID,…}.dse` | 51 motion or body-type models, up to 6 MB each |
  | `/debug/…` | 37 files, including an uncompressed Goku model plus attack animations (`/debug/goku/*.dse`) and debug primitives (`cube_100B.dse`, `cylinder_100M.dse`) |
  | `/scene/scn*.bin` | 26 files |
  | `/eff/*.dsf` | effects |
  | `/archiveDBK.dsa` | 16.7 MB archive (`DSA `, v1). Holds **BIOS-format LZ77 streams (type `0x10`)**, including the resident character models (see below). The table at `0x10` has 12-byte entries; the first field is an offset (entry *n*'s offset + second field = entry *n*+1's offset). The other fields aren't understood yet. It contains no LZMA streams |
  | `/sound/data.sdat` | sound |

## Where the title-screen model lives
- The main RAM dump at frame 3180 contains 16 `DSE\0` headers:
  - Header-sized copies of the `/smot` models at `0x02250EA4`, `0x02254798`, `0x02259ADC` and
    `0x0225D75C`. These match `sm_110000_TALL`, `sm_120000_BOY`, `sm_180000_KID` and
    `sm_100000_NORMAL` by their first 64 bytes; only the headers are resident.
  - Eleven models between `0x02265524` and `0x023184F0` whose headers don't match any
    standalone NitroFS file, raw or decompressed. One of them is the title Goku at
    **`0x022CB6E4`**, size `0x2A370`. It comes from inside `archiveDBK.dsa` (next section).
- That model is written between frames 1436 and 1441. At frame 1436 the region reads
  `01 04 01 04`; from frame 1441 on it reads `DSE\0`.
- Goku's texture atlases (three pal256 128×128 textures at texture VRAM `0x00500`, `0x04500` and
  `0x08500`) are also resident in RAM, at `0x02374564`, `0x02378564` and `0x0237C564`
  (`0x4000` apart). Their palettes (texture palette VRAM `0x13E0`/`0x15E0`/`0x17E0`) aren't
  in RAM at frame 3180, so they were probably freed after upload.

## How the title-screen model is loaded (found with GDB watchpoints)
- **The file:** the LZ77 stream at ROM `0x4D0012` = **`/archiveDBK.dsa + 0x3A1A12`**.
  - Header `10 70 A3 02`: type `0x10`, 0x2A370 bytes uncompressed.
  - 79,236 compressed bytes decompress to exactly 172,912 bytes, starting `DSE\0 "30" …`.
  - Table entry #2813's first field is `0x3A1A00`, 0x12 bytes before the stream.
- **Reading it in:** the compressed stream is read into a work buffer at **`0x02374564`** by
  frame 1438 (at frame 1436 that buffer held other archive data, from `+0x954872`).
  - After the model is built, the same buffer holds Goku's texture atlases, which is why the
    texels were found there at frame 3180.
- **The decompressor:** a software LZ77 decoder in main RAM at **`0x020C2A48`–`0x020C2AF8`**:
  - `tst lr,#0x80` walks the flag bits MSB-first
  - the literal path is `ldrb r6,[r0],#1` / `swpb r6,r6,[r1]`, byte stores done with `swpb`
  - the back-reference path starts at `0x020C2A7C`
  - registers during the model decode: `r0` = compressed source, `r1` = destination,
    `r2` = bytes left (0x2A36D after the 3rd byte)
  - the stack frame holds `0x02374564` (source) and `0x022CB6E4` (destination)
- **Timeline at the destination `0x022CB6E4`:**
  - frame 1436: the same routine first writes another stream there (`01 04 01 04 21 08 44 08…`,
    source around `0x0236EFA5`)
  - frame 1441: the model decode writes `DSE\0`
- **Patched after load:** by frame 3180, only 31,810 of 43,228 words match the decompressed file.
  The header changes from offset 4 onward (for example the `0x06` flag byte goes from `0x01` to
  `0x41`), so the game patches models in place after loading them.
- **To reproduce:** `ds-probe ROM --frames 3000 --gdb-nobreak &` then
  `gdb-multiarch -batch -ex "target remote :3333" -ex "watch *(unsigned int*)0x022CB6E4 if *(unsigned int*)0x022CB6E4 == 0x00455344" -ex continue -ex "info registers" -ex 'x/12i $pc-24'`.

## Open questions / next steps
1. **Decode `archiveDBK.dsa`'s entry table.** Work out the meaning of the second and third
   fields and whether names exist, so every LZ77 entry can be listed and unpacked. Every `DSE`
   in RAM should then be traceable to an entry.
2. **Find the loader's caller** of the LZ77 routine at `0x020C2A48`: set a breakpoint at its
   entry and read `lr`. That should lead to the archive lookup (entry id → offset) and the
   post-load patching that changes about 26% of the model's words.
3. **Decode the rest of the DSE header's section table**: bone matrices, texture and palette
   blocks, and material to TEXIMAGE_PARAM mapping. Useful references: `/debug/goku/goku.dse`
   (uncompressed) and `/debug/cube_100B.dse` (a trivial model).
4. **Reach a battle** (name entry → menu) and dump a two-fighter frame. Check the
   2048-polygon limit (DISP3DCNT bit 13) and whether battle models use the same DMA path.
