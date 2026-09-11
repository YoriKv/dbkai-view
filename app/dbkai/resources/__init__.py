"""Access to bundled, read-only assets shipped inside the package.

Keeping them *inside* ``dbkai`` (rather than in a sibling data directory) means
they are packaged into the wheel and collected into a frozen build.

Paths are resolved with :mod:`importlib.resources`, not relative to
``__file__``: PyInstaller relocates package data into a temporary directory, so
a ``__file__``-relative lookup finds nothing in a release build while working
perfectly in a checkout.

    from dbkai import resources
    data = resources.read_bytes("icons", "app.png")
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable

_ANCHOR = "dbkai.resources"


def resource(*parts: str) -> Traversable:
    """Return a Traversable for ``dbkai/resources/<parts...>``."""
    node = files(_ANCHOR)
    for part in parts:
        node = node / part
    return node


@cache
def read_bytes(*parts: str) -> bytes:
    """Read a bundled resource as bytes.

    Cached: bundled data cannot change while the app runs.
    """
    return resource(*parts).read_bytes()


def read_text(*parts: str, encoding: str = "utf-8") -> str:
    """Read a bundled text resource."""
    return resource(*parts).read_text(encoding=encoding)
