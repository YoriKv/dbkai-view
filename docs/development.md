# Working in this repository

Environment, tooling and test setup.

## Python, uv and the two venvs

**Python 3.12** (`.python-version`), managed with `uv`. The app is a console
script in `pyproject.toml`: `uv run dbkai`.

The checkout keeps **one venv per OS, side by side**:

| | |
|---|---|
| `.venv` | Windows — the default name, so PyCharm auto-detects it |
| `.venv-linux` | WSL/Linux — selected with `UV_PROJECT_ENVIRONMENT=.venv-linux` |

A bare `uv sync` or `uv run` under WSL targets `.venv` and would overwrite the
Windows environment with Linux binaries. `.envrc` exports
`UV_PROJECT_ENVIRONMENT=.venv-linux`, so `direnv` handles it on `cd` — install
it (`sudo apt install direnv`, then `eval "$(direnv hook bash)"` at the end of
`~/.bashrc`) and run `direnv allow` once. Without direnv, export it yourself.
Neither venv is committed; `uv.lock` is. Editing `.envrc` invalidates direnv's
trust, so run `direnv allow` again afterwards.

To create or refresh each one:

```bash
# WSL
UV_PROJECT_ENVIRONMENT=.venv-linux uv sync
# Windows (PowerShell, from the checkout), or from WSL via interop:
uv sync
env -u UV_PROJECT_ENVIRONMENT uv.exe sync
```

**A non-interactive shell has none of that.** `~/.bashrc` returns early when
there is no terminal, above the direnv hook, so anything that shells out gets
the default `.venv`. Load it explicitly at the top of such a call:

```bash
eval "$(direnv export bash 2>/dev/null)"
```

`.claude/settings.json` does exactly that before every `uv`, `qmd`, `pytest`,
`ruff` or `python` command Claude Code runs.

## Documentation search (qmd)

`docs/` is indexed as the **`dbkai`** collection of
[qmd](https://github.com/tobi/qmd), installed with bun into `~/.bun/bin`.
`.envrc` puts that directory on `PATH`, since `~/.bashrc` only does so for
interactive shells.

```bash
qmd collection add docs --name dbkai    # once per machine
qmd search -c dbkai "<exact terms>"     # BM25 keywords, works immediately
qmd query -c dbkai "<question>"         # hybrid search; needs the models
qmd update && qmd embed                 # re-index after editing docs
```

`qmd query` and `qmd embed` need local embedding and reranking models;
`qmd pull` downloads them once.

## Line endings

**LF everywhere.** `* text=auto eol=lf` in `.gitattributes` normalises on commit
and writes LF on checkout. Game data and the app's icons are marked `-text`
explicitly rather than relying on binary auto-detection.

## WSL

- **Keep paths relative to the repository**; never hardcode `/mnt/c` or `C:\`.
- **The GUI runs under WSLg.** If Qt cannot find a platform plugin, set
  `QT_QPA_PLATFORM=wayland` (or `xcb`); the tests force `offscreen` themselves.
- **Scratch goes in `tmp/` inside the repository**, not `/tmp`.

## PyCharm

The project opens from Windows at `Z:\dbkai` (the WSL `~/dev` share mapped to
`Z:`), and the committed `.idea/` files configure it:

- `app/` is the source root and `app/tests/` the test root, so `dbkai` imports
  resolve; the venvs, caches and game-data folders are excluded.
- The interpreter is the SDK named `Z:\dbkai\.venv\Scripts\python.exe`. On a
  machine that has not registered it yet, add it once (*Settings > Python
  Interpreter > Add > Existing*, pointing at that file) — PyCharm keeps the
  project bound to that name.
- The test runner is pytest, and docstrings are plain text.
- `.run/` holds shared run configurations: **DBKai** launches
  `python -m dbkai`, **Tests** runs `app/tests`.

## Tests

```bash
uv run pytest                  # the whole suite, in parallel
uv run pytest -n0 -s <test>    # one process, for a breakpoint or live output
```

**The suite runs in parallel by default** — `-n auto --maxprocesses=8`, set in
`addopts`. `-n0` puts everything back in this process.

- **Qt tests run offscreen**, forced by `app/tests/conftest.py`. Under the
  offscreen platform a modal's `exec()` never returns, so a new modal dialog
  needs an escape hatch in that file.
- **The suite never touches the developer's preferences.** `conftest.py`
  redirects the store through `dbkai.ui.settings.use_store`, per process, and
  empties it before every test.
- **Don't verify UI changes by screenshot.** The offscreen render is not what
  the user sees; assert on behaviour.

## Lint and format

```bash
uv run ruff check .
uv run ruff format --check .
```

Config lives in `pyproject.toml`.
