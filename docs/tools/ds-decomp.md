# ds-decomp (`dsd`)

[ds-decomp](https://github.com/AetiasHax/ds-decomp) is a Rust toolkit for
taking a DS ROM apart into its code modules, analysing them (function bounds,
sections, relocations, symbols) and disassembling them. Here it is the way to
read the game's code — the model decoders and file loaders — and it has nothing
to do with building the app.

## Setup

The checkout lives next to this one at `../ds-decomp`, and the build needs a
Rust toolchain (edition 2024, so Rust ≥ 1.85, from
[rustup](https://rustup.rs)). The binary is `../ds-decomp/target/release/dsd`.

```bash
cargo build --release --manifest-path ../ds-decomp/Cargo.toml
```

**The checkout carries one local patch**, in `lib/src/config/module.rs`
(`add_dsprot_bss_and_relocations`): a DS Protect relocation whose target lies
outside its overlay is recorded as a relocation to `main` instead of aborting
`init`. Overlay 0 of this game has one such pointer (from `0x021282fc` to
`0x020c5954` in the ARM9 binary), and upstream `dsd` refuses to analyse the ROM
without the patch. Re-apply it after updating the checkout, or upstream it.

## Layout

Everything `dsd` produces is game data and lives in the gitignored
`reference/dsd/`:

```
reference/dsd/
├── rom/            dsd rom extract: header.yaml, arm9/, arm7/, arm9_overlays/,
│                   banner/ and files/ (the NitroFS tree; same content as reference/fs/)
├── config/arm9/    dsd init: config.yaml, delinks.txt, symbols.txt, relocs.txt,
│                   plus itcm/, dtcm/ and overlays/ovNNN/ with the same three files
├── asm/            dsd dis: one .s file per module
└── build/delinks/  dsd delink: one relocatable ELF (.o) per module
```

The config files are `dsd`'s own formats, documented in
`../ds-decomp/docs/` (`delinks.md`, `symbols.md`, `relocs.md`). `symbols.txt`
is where a function gets a name: rename `func_020c56d0` there, and the next
`dsd dis` uses it everywhere.

## Commands

The ROM is at `../Dragon Ball Kai - Ultimate Butou Den (Japan).nds`. Its
secure area is already decrypted, so no ARM7 BIOS is needed. Each step takes
well under a second.

```bash
DSD=../ds-decomp/target/release/dsd

# 1. Split the ROM into modules and files
$DSD rom extract --rom "../Dragon Ball Kai - Ultimate Butou Den (Japan).nds" \
    --output-path reference/dsd/rom

# 2. Analyse the ARM9 code: sections, functions, relocations, symbols
$DSD init --rom-config reference/dsd/rom/config.yaml \
    --output-path reference/dsd/config --build-path reference/dsd/build

# 3. Disassemble every module (regenerate after editing symbols.txt)
$DSD dis --config-path reference/dsd/config/arm9/config.yaml --asm-path reference/dsd/asm

# 4. Optional: relocatable ELF objects, for objdiff or a disassembler that reads ELF
$DSD delink --config-path reference/dsd/config/arm9/config.yaml
```

`init` prints warnings about calls into the middle of `func_020cda8c` and
`func_020cdea0` (tail-call entry points; it adds labels for them) and the DS
Protect warning from the patch above. Both are expected.

What is not useful here:

- `dsd sig apply --all` matches nothing: the only shipped signatures are
  `FS_LoadOverlay`/`FS_UnloadOverlay`, and neither is found in this binary.
- `dsd rom build` reproduces the ROM except for four header bytes and the
  banner block at `0x12dc00`–`0x12dfff` (checksums), which is enough to
  confirm the extraction is faithful; nothing here needs a rebuilt ROM.
- `lcf`, `objdiff`, `check`, `apply`, `diff`: matching-decompilation workflow,
  not used.

## Reading the output

- `asm/_dsd_gap@main_7.s` is the ARM9 binary, `asm/_dsd_gap@itcm_1.s` the
  ITCM code (fast paths such as the DMA and GX helpers), and
  `asm/_dsd_gap@ovNNN_*.s` the overlays. A function is `func_<address>` until
  it is named.
- `config/arm9/relocs.txt` lists every resolved reference between modules —
  the quickest way to find which main-binary functions an overlay calls, or
  which code touches a data address.
- The module map and what each overlay slot holds are in
  [`formats/rom.md`](../formats/rom.md).
