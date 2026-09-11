# dbkai

> **DBKai**, a model extractor and viewer for DB Kai: Ultimate Butoden —
> Python 3.12 + Qt (PySide6), Windows/macOS/Linux.
>
> *Maintaining this file:* it is an **index of operating rules, not a knowledge
> base**, and carries only what applies to most work here. Anything longer, or
> specific to one subject, belongs under [`docs/`](docs/README.md). Rules only,
> in the present tense — no history, no anecdotes.

## Repository shape

```
dbkai/
├── app/
│   ├── dbkai/        the package: app.py bootstrap, ui/, resources/
│   └── tests/
├── docs/             reference documentation
├── .claude/          project settings for Claude Code
└── (root)            pyproject.toml + shared config
```

Paths here are repository-relative, and commands work from any directory.

## Environment

`uv run dbkai` runs the viewer.

- **On WSL, `uv` must target `.venv-linux`.** `.venv` is the Windows environment,
  so a bare `uv sync` / `uv run` would overwrite it with Linux binaries. `.envrc`
  exports it for direnv; a shell that has not loaded direnv — any non-interactive
  one — needs `eval "$(direnv export bash 2>/dev/null)"` first, or
  `UV_PROJECT_ENVIRONMENT=.venv-linux` exported by hand.
- **Keep paths repository-relative** — never `/mnt/c` or `C:\` — and write LF,
  everywhere.
- **Scratch goes in `tmp/`, not `/tmp`.**

[`docs/development.md`](docs/development.md) has the setup behind these — the two
venvs, PyCharm, qmd, the tests, lint and format.

## Finding things

- **Python → the `pycharm` MCP** when it is connected; grep when it is not.
- **Documentation → QMD**, which indexes `docs/` as the `dbkai` collection.
  Check it before re-deriving anything it covers.

  ```bash
  qmd query -c dbkai $'intent: <what you are looking for>\nlex: <exact terms>\nvec: <paraphrase>'
  qmd search -c dbkai "<exact terms>"    # keywords only, no models needed
  qmd update && qmd embed                # re-index after adding or editing docs
  ```

## Working rules

- **Before calling a change done**: `uv run pytest`, `uv run ruff check .` and
  `uv run ruff format --check .`.
- **Never commit unless explicitly asked.** Leave finished work in the tree and
  say it is ready.
- **Game data never enters the repository.** Dumps, and anything extracted from
  them, live in the gitignored `reference/` and `extracted/`. Tests use
  synthetic fixtures, not real game files.
- **Ask before going to the web, and ask again before downloading from it.**

## Architecture boundaries

- **Only `dbkai.ui` and `dbkai.app` import Qt.** Everything else — the archive
  readers, model decoders and exporters — stays Qt-free and headless-testable;
  turning model data into something Qt draws is the `ui` side's job.
- **The theme is a palette, not a stylesheet.** Colour a widget from a palette
  role, never a literal, or it breaks in one of the two themes —
  [`docs/app/theme.md`](docs/app/theme.md).
- **Read a preference through its typed accessor** in `dbkai.ui.settings`,
  never through `settings().value()`.
- **The repository is MIT-licensed**, so a new dependency must be usable under
  it: MIT, BSD, Apache-2.0 and LGPL (dynamically linked) are; GPL is not.

## Writing it down

- **Put the finding where it belongs.** A fact about the game's formats goes in
  `docs/formats/`; a decision about the app in `docs/app/`.
- **Never write it to an assistant memory.** Anything worth keeping goes in this
  repository, where it is reviewed and versioned.
- **Say it once, shortly, and in the present tense.**
