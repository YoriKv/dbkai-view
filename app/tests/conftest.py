"""Shared setup for the tests.

Forces Qt's ``offscreen`` platform before PySide6 is imported anywhere, so the
suite runs headless in CI and under WSL without a display server. Export
``QT_QPA_PLATFORM`` yourself to override it.

**Offscreen turns every modal into a hang**: ``exec()`` never returns without a
windowing system to answer it, and a test that reaches one wedges the whole run
*after* its own body has passed. A new modal dialog needs an escape hatch here.
"""

import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from dbkai import APP_ID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: The store this run works on. Named after the app so it is recognisable if it
#: is ever left behind, and **per process** because xdist runs several at once --
#: a shared name would have concurrent workers emptying each other's
#: preferences between tests, which reads as a flake.
TEST_STORE = (f"{APP_ID}-tests", f"pytest-{os.getpid()}")


@pytest.fixture(scope="session", autouse=True)
def _test_settings_store():
    """Send the suite's preferences somewhere that is not the developer's.

    Goes through the application's own :func:`~dbkai.ui.settings.use_store`
    rather than through Qt, so it keeps the same backend the app uses -- a
    registry key on Windows, a file elsewhere.
    """
    from dbkai.ui.settings import settings, use_default_store, use_store

    use_store(*TEST_STORE)
    yield
    # Leave nothing behind: a key per process would otherwise accumulate under
    # HKCU\Software on Windows, one for every run.
    store = settings()
    store.clear()
    store.sync()
    path = Path(store.fileName())
    # A registry-backed store's "file name" is a registry path, not a file.
    if path.is_file():
        path.unlink()
        # Qt nests the file one directory down, named for the organization.
        # Guarded on being empty: never take anything else with it.
        with suppress(OSError):
            path.parent.rmdir()
    use_default_store()


@pytest.fixture(autouse=True)
def _empty_settings(_test_settings_store):
    """Empty the store before every test, so no test decides the next one's
    starting state. Guarded so a non-Qt test does not pull Qt in."""
    if "PySide6.QtCore" not in sys.modules:
        return
    from dbkai.ui.settings import settings

    settings().clear()


@pytest.fixture(autouse=True)
def _help_never_blocks(monkeypatch):
    """Make Help > About and Help > Shortcuts return instead of blocking. The
    guide is still built, so a test can assert on what it was built from."""
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtWidgets import QMessageBox

    from dbkai.ui.shortcuts import ShortcutGuide

    monkeypatch.setattr(QMessageBox, "about", lambda *args: None)
    monkeypatch.setattr(ShortcutGuide, "exec", lambda self: 0)


@pytest.fixture(autouse=True)
def _destroy_widgets_between_tests():
    """Actually destroy the windows pytest-qt closed, before the next test.

    ``qtbot.addWidget`` cleanup ends in ``deleteLater()``, which only runs once
    an event loop spins - and these tests never spin one. Flushing the
    deferred-delete queue keeps a test's cost the same wherever it sits.
    """
    yield
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None:
        return
    app = sys.modules["PySide6.QtWidgets"].QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, qtcore.QEvent.Type.DeferredDelete)


@pytest.fixture
def window(qtbot):
    """A main window, closed by pytest-qt afterwards."""
    from dbkai.ui.main_window import MainWindow

    widget = MainWindow()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Turn the app's message boxes into exceptions.

    Under the offscreen platform a modal's ``exec()`` never returns, so a test
    that reaches one wedges the run. Apart from the Help menu, which
    :func:`_help_never_blocks` answers, the window only opens a message box
    to report a failure, which a test would rather see as a traceback.
    """
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtWidgets import QMessageBox

    def raise_instead(*args, **_kwargs):
        raise AssertionError(f"a message box opened: {args[1:]}")

    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(raise_instead))
