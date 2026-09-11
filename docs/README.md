# DBKai documentation

`CLAUDE.md` at the repository root is an index of operating rules. This folder
is where the substance lives.

This tree is indexed as the **`dbkai` QMD collection**. Search it rather than
grepping:

```bash
qmd query -c dbkai $'intent: <what you are looking for>\nlex: <exact terms>\nvec: <paraphrase>'
qmd search -c dbkai "<exact terms>" -n 10
qmd update && qmd embed        # re-index after adding or editing docs
```

## Topic index

- **[`development.md`](development.md)** — working in this checkout: the two
  venvs, WSL, PyCharm, the tests, lint and format.
- **[`app/`](app/)** — the viewer's design decisions:
  [`app/viewer.md`](app/viewer.md) for the session, viewport and panels,
  [`app/theme.md`](app/theme.md) for the palette.
- **[`formats/`](formats/)** — what the game's files contain, as it is worked
  out. [`formats/rom.md`](formats/rom.md) is the cartridge itself: code
  modules, overlay slots, file system. [`formats/archive.md`](formats/archive.md)
  is `archiveDBK.dsa`, [`formats/dse.md`](formats/dse.md) the model, motion
  and texture container, [`formats/gx.md`](formats/gx.md) the geometry
  commands and fixed-point conventions, [`formats/dsa.md`](formats/dsa.md)
  the action files and the visibility presets that drive which parts show,
  and [`formats/compression.md`](formats/compression.md) the three codecs.
- **[`tools/`](tools/)** — external tools and how this project uses them.
  [`tools/ds-decomp.md`](tools/ds-decomp.md) disassembles the game's code;
  [`tools/melonds.md`](tools/melonds.md) runs it and watches what it draws.

## Conventions

- **Describe the present.** Every document states the current state — not
  history. When a change makes a document wrong, fix it in the same change.
- **Verified, not plausible.** Document what the data demonstrably is. Mark an
  inference as an inference.
