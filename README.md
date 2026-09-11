# DBKai

A model extractor and viewer for **DB Kai: Ultimate Butoden**.

Built on Python + Qt (PySide6); runs on Windows, macOS and Linux.

## Running

```bash
uv run dbkai [ROM.nds]       # the viewer; or: uv run python -m dbkai
uv run python -m dbkai.cli list ROM.nds              # what is inside
uv run python -m dbkai.cli extract ROM.nds out/ --motion   # everything as glTF + PNG
uv run pytest                # the tests force Qt's offscreen platform
```

Open a ROM, pick a model in the Assets dock, and the viewport shows it posed
by its body type's motion set. The Parts tab switches the faces, hands and
costume pieces the game layers on one model; File > Export writes glTF 2.0
with skeleton, textures and animation clips.

On WSL, `uv` must target `.venv-linux` — see
[`docs/development.md`](docs/development.md).

## What is here

```
app/
├── dbkai/
│   ├── app.py        QApplication bootstrap; with ui/, the only Qt importer
│   ├── cli.py        list / extract / export from the command line
│   ├── game.py       a ROM's assets catalogued, with their relations
│   ├── nds/          the cartridge image and its NitroFS
│   ├── formats/      decoders: archive, DSE, geometry commands, textures, compression
│   ├── model/        skeleton, animation, render-ready meshes (numpy, no Qt)
│   ├── export/       glTF 2.0 and PNG writers
│   ├── resources/    bundled read-only assets
│   └── ui/           everything Qt: session, viewport, panels, main window
└── tests/            synthetic fixtures; no game data
```

The file formats are documented under [`docs/formats/`](docs/formats/).

Game data is never part of this repository: supply your own dump.

## Licence

MIT, with the full text in [`LICENSE`](LICENSE). Qt is used through PySide6
under the LGPLv3.
