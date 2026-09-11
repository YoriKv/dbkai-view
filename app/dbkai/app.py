"""Application bootstrap: construct the QApplication and show the main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from dbkai import APP_ID, APP_NAME, __version__, configure_logging, resources
from dbkai.ui.main_window import LAST_ROM_KEY, MainWindow
from dbkai.ui.settings import load_enum_setting, load_str_setting
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme
from dbkai.ui.viewport import request_surface_format

# Only the application name is set on the QApplication, never an organization:
# QStandardPaths appends both, so an organization equal to the app would nest
# the data directory as dbkai/dbkai. The preference store names its own
# identity rather than inheriting this one (dbkai.ui.settings.settings).


def main(argv: list[str] | None = None) -> int:
    """Entry point for both ``dbkai`` and ``python -m dbkai``."""
    # Before anything that might log, so the first load is not the one missed.
    configure_logging()
    # The viewport's OpenGL version has to be asked for before the application
    # exists; the default format is read when the first context is made.
    request_surface_format()
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
    # A ROM named on the command line, else the one opened last time.
    args = [
        a for a in (argv if argv is not None else sys.argv[1:]) if not a.startswith("-")
    ]
    path = args[0] if args else load_str_setting(LAST_ROM_KEY)
    if path and Path(path).is_file():
        if path.lower().endswith(".nds"):
            window.open_rom(path)
        else:
            window.open_file(path)
    _install_screenshot_hook(app, window)
    return app.exec()


#: Set to a PNG path to have the app load ``DBKAI_ASSET`` (an asset path in the
#: ROM), grab the viewport after a moment, write it there and quit. A hook for
#: checking rendering from a script; it does nothing in normal use.
SCREENSHOT_ENV = "DBKAI_SCREENSHOT"
ASSET_ENV = "DBKAI_ASSET"


def _install_screenshot_hook(app: QApplication, window: MainWindow) -> None:
    import os

    target = os.environ.get(SCREENSHOT_ENV)
    if not target:
        return
    from PySide6.QtCore import QTimer

    def load() -> None:
        asset = os.environ.get(ASSET_ENV)
        if asset and window.session.game is not None:
            found = window.session.game.find(asset)
            if found is not None:
                window.session.load_asset(found)
        if os.environ.get("DBKAI_MOTION") == "bind":
            window.session.set_motion(None)
        frame = os.environ.get("DBKAI_FRAME")
        if frame and window.session.clip is not None:
            window.session.set_clip_frame(int(frame))
        camera = os.environ.get("DBKAI_CAMERA")
        if camera:
            yaw, pitch = (float(v) for v in camera.split(","))
            window.viewport.camera.yaw = yaw
            window.viewport.camera.pitch = pitch
            window.viewport.update()

    def grab() -> None:
        window.viewport.grab_image().save(target)
        app.quit()

    QTimer.singleShot(500, load)
    QTimer.singleShot(2500, grab)


if __name__ == "__main__":
    raise SystemExit(main())
