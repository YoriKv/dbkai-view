# DBKai

A model extractor and viewer for **DB Kai: Ultimate Butoden**.

Built on Python + Qt (PySide6); runs on Windows, macOS and Linux.

## Running

```bash
uv run dbkai                 # or: uv run python -m dbkai
uv run pytest                # the tests force Qt's offscreen platform
```

On WSL, `uv` must target `.venv-linux` — see
[`docs/development.md`](docs/development.md).

## What is here

```
app/
├── dbkai/
│   ├── app.py        QApplication bootstrap; with ui/, the only Qt importer
│   ├── resources/    bundled read-only assets
│   └── ui/           everything Qt: main_window.py, theme.py, settings.py
└── tests/
```

Game data is never part of this repository: supply your own dump.

## Licence

MIT, with the full text in [`LICENSE`](LICENSE). Qt is used through PySide6
under the LGPLv3.
