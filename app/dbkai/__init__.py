"""DBKai - a model extractor and viewer for DB Kai: Ultimate Butoden.

The package is laid out so that only :mod:`dbkai.ui` (and the :mod:`dbkai.app`
bootstrap) imports Qt. Every other module stays Qt-free, so the extractor side
is testable and reusable headless.

- :mod:`dbkai.nds` - the cartridge: header and NitroFS file system.
- :mod:`dbkai.formats` - the game's file formats, parsed into plain data.
- :mod:`dbkai.model` - models, skeletons, motions and actions, ready to pose.
- :mod:`dbkai.export` - glTF and PNG writers.
- :mod:`dbkai.game` - one ROM's assets, catalogued and related.
- :mod:`dbkai.extract` - writing assets to disk, for the CLI and the viewer.
- :mod:`dbkai.cli` - the headless command line, ``python -m dbkai.cli``.
- :mod:`dbkai.app` - QApplication bootstrap and entry point.
- :mod:`dbkai.ui` - the Qt front end: window, session, viewport and panels.
- :mod:`dbkai.resources` - bundled read-only assets, resolved so they survive a
  frozen release build.
"""

__version__ = "0.1.1"

# The name shown to a person: window titles, dialogs, the About box, and
# QApplication.applicationDisplayName.
APP_NAME = "DBKai"

# The name the *platform* files data and preferences under (application-data
# directory, QSettings). Deliberately not APP_NAME: this string is never shown,
# and lower case matches the distribution name, so a person looking at
# ~/.config or %APPDATA% sees the project they installed. Changing it orphans
# whatever the previous id filed away.
APP_ID = "dbkai"

#: Turns on the package's own logging, which is otherwise silent: set it to a
#: level name (``DEBUG``, ``INFO``) or to ``1`` for ``DEBUG``; ``0`` leaves it off.
DEBUG_ENV = "DBKAI_DEBUG"


def configure_logging(value: str | None = None) -> int | None:
    """Install a stderr handler for this package's loggers, if asked for.

    Returns the level applied, or ``None`` when the variable is unset and
    nothing was configured. Only ``dbkai``'s own logger is touched, so turning
    it on does not make Qt or anything else start talking. A library module
    never calls this: installing a handler is a decision for the application.
    """
    import logging
    import os

    raw = (value if value is not None else os.environ.get(DEBUG_ENV, "")).strip()
    if raw.lower() in {"", "0", "false", "no", "off"}:
        return None
    # A bare truthy value means "as much as there is"; a name means that level.
    level = logging.DEBUG if raw.lower() in {"1", "true", "yes", "on"} else raw.upper()
    logger = logging.getLogger(__name__)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    try:
        logger.setLevel(level)
    except ValueError:
        # An unusable value is worth saying so about rather than falling back
        # silently -- the point of setting it was to see more, not less.
        logger.setLevel(logging.DEBUG)
        logger.warning("%s=%r is not a level name; using DEBUG", DEBUG_ENV, raw)
    return logger.level
