"""Application bootstrap: construct the QApplication and show the main window."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from dbkai import APP_ID, APP_NAME, __version__, configure_logging, resources
from dbkai.ui.main_window import MainWindow
from dbkai.ui.settings import load_enum_setting
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme

# Only the application name is set on the QApplication, never an organization:
# QStandardPaths appends both, so an organization equal to the app would nest
# the data directory as dbkai/dbkai. The preference store names its own
# identity rather than inheriting this one (dbkai.ui.settings.settings).


def main(argv: list[str] | None = None) -> int:
    """Entry point for both ``dbkai`` and ``python -m dbkai``."""
    # Before anything that might log, so the first load is not the one missed.
    configure_logging()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)

    # Style and palette both come from the theme, and both before the window is
    # built: a widget that bakes a palette color into a pixmap should rasterize
    # it once, in the color it will actually be shown in. View > Theme switches
    # it live afterwards.
    apply_theme(load_enum_setting(THEME_KEY, Theme.LIGHT))

    # Loaded from bytes rather than a file path so it resolves identically in a
    # source checkout and in a frozen build, where resources live in the bundle.
    icon = QPixmap()
    icon.loadFromData(resources.read_bytes("icons", "app.png"))
    app.setWindowIcon(QIcon(icon))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
