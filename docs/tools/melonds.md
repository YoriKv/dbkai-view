# melonDS and ds-probe

[melonDS](https://github.com/melonDS-emu/melonDS) is a DS emulator (C++17,
CMake). The checkout at `../melonDS` adds **`tools/ds-probe`**, a headless
harness that runs a ROM for N frames and dumps what the 3D engine was fed,
and a GDB stub with working watchpoints. Here it is the way to watch the game
load, patch and draw a model — the ground truth the app's decoders are checked
against. `../melonDS/CLAUDE.md` is the tool's own reference: every option, the
3D pipeline inside the core, and how to instrument it. This page is the part
that applies to this project.

## Setup

```bash
# once: build tools and Qt6 (the full apt line is in ../melonDS/CLAUDE.md §1)
cmake -S ../melonDS -B ../melonDS/build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DENABLE_JIT=OFF -DBUILD_DS_PROBE=ON
cmake --build ../melonDS/build -j$(nproc)
```

That gives `../melonDS/build/ds-probe` (headless) and `../melonDS/build/melonDS`
(the GUI, under WSLg). `-DENABLE_JIT=OFF` is not optional: with the JIT on, the
GDB stub and no$gba debug prints are silently disabled. No BIOS or firmware
dump is needed; FreeBIOS direct-boots the cart. `gdb-multiarch` is the debugger
(`apt install gdb-multiarch`).

**The checkout carries local changes** beyond upstream: the `BUILD_DS_PROBE`
option and `tools/ds-probe` itself, and GDB watchpoint support in the CPU core
(`ARM::GdbRecordWatch`). Pulling upstream means keeping those.

## Layout

ds-probe writes wherever `--out` points. Runs are scratch and go under
`tmp/probe/<run>/`; savestates that mark a milestone (title screen, save
select, a battle) are kept there as well and reused with `--load-state`, so the
boot is not replayed for every experiment. Nothing under `tmp/` is committed.

```
tmp/probe/<run>/
├── frames.csv             always: frame, GX FIFO words, rendered polygons, DISP3DCNT
├── screen_01800.png       --shot: both screens, top above bottom (256×384)
├── trace_00005.txt        --trace: every geometry command of frame 5, with its sender
├── poly_00005.txt, tex/   --dump3d: the polygon list drawn in frame 6 and its textures
├── mainram_00005.bin      --dumpram: 4 MiB of main RAM (file offset 0 = 0x02000000)
└── texvram_00005.bin, texpal_00005.bin   --dumpvram: texture and palette VRAM
```

Frame numbers in file names are zero-padded to five digits.

## Commands

The ROM is at `../Dragon Ball Kai - Ultimate Butou Den (Japan).nds`. ds-probe
runs the interpreter with the software renderer, so runs are deterministic:
the frame numbers below hold on every machine, counted from power-on with no
save file. It runs at roughly 350 fps, so the whole boot is under ten seconds.

```bash
PROBE=../melonDS/build/ds-probe
ROM="../Dragon Ball Kai - Ultimate Butou Den (Japan).nds"

# Boot to the title screen (3D Goku from ~frame 1560), save a milestone state
$PROBE "$ROM" --frames 1800 --shot-every 60 --save-state 1800:tmp/probe/title.mln --out tmp/probe/boot

# Continue from it: press START, reach save-data select
$PROBE "$ROM" --load-state tmp/probe/title.mln --frames 1000 --press 1:START --shot-every 60 --out tmp/probe/menu

# Dump the 3D of one frame: polygons, textures, command trace, main RAM
$PROBE "$ROM" --load-state tmp/probe/title.mln --frames 10 --dump3d 5 --trace 5 --dumpram 5 --out tmp/probe/title3d

# Debug: GDB stub on :3333 (ARM9), the run keeps going until you attach
$PROBE "$ROM" --frames 100000 --gdb-nobreak --out tmp/probe/gdb &
gdb-multiarch -q -batch -ex "target remote :3333" \
    -ex "watch *(unsigned int*)0x022CB6E4 if *(unsigned int*)0x022CB6E4 == 0x00455344" \
    -ex continue -ex "info registers" -ex 'x/12i $pc-24' -ex detach
```

Frame numbers restart at 1 after `--load-state`. Keys for `--press` are
`A B X Y L R START SELECT UP DOWN LEFT RIGHT`, joined with `+`; a press lasts
six frames unless given a length.

## Reading the output

- **Find the frame first.** `frames.csv` tells which frames draw 3D and how
  much; aim `--dump3d` and `--trace` at one of those. The polygon list dumped
  at frame F is what the screen shows in F+1, so compare it to the F+1 screenshot
  (`--dump3d` writes both).
- **Each trace line is `<source> <COMMAND> <params>`.** A `dmaN/imm src=0xADDR`
  source means the display list was DMA'd from `ADDR` in main RAM — that is
  packed model data, and `--dumpram` plus a search for the bytes finds the
  containing asset and its header. A `cpu pc=0xADDR` source is a store
  instruction; `0x01FFxxxx` is ITCM. Look the address up in the
  [ds-decomp](ds-decomp.md) disassembly, or break on it in GDB.
- **Watchpoints report after the access**, so `$pc` is the next instruction
  and the store is at `$pc-4` (ARM) or `$pc-2` (Thumb). They catch CPU
  accesses only, not DMA, and `watch` fires only when the value changes.
- **Addresses in overlays** (from `0x021282c0`) only mean something while that
  overlay is loaded; the slots are in [`formats/rom.md`](../formats/rom.md).

What this project does not use: the GUI beyond eyeballing (it rewrites its
config on exit and cannot be screenshotted under Xvfb), the OpenGL and compute
renderers (approximations; the software renderer is the reference), and
persisted saves (ds-probe has none — use savestates).
