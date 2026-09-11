"""The app-wide preference store, and typed readers for it.

One :class:`QSettings` object identified explicitly rather than inherited from
the :class:`QApplication`: Qt's no-argument constructor reads
``organizationName``/``applicationName`` off the running application, which
means the store a test or a script gets depends on whether a bootstrap happened
to run first. Naming it here makes the location the same everywhere, and lets
:func:`dbkai.app.main` leave the organization unset (see the note there).

Preferences here are properties of the *person*, not of what they have open -
theme, window geometry, view toggles. Anything belonging to a document belongs
in the document.

**Read a preference through the typed accessor for its type, never through
``settings().value()``.** The store keeps text, not Python objects: a ``True``
written here comes back out of the INI file - and out of the Windows registry,
where the app's native store is - as the string ``"true"``, so an untyped read
yields a value that is truthy, is not ``True``, and passes every casual test of
it. The trap hides itself, because within one process Qt's config cache answers
from the object that was written rather than from the file; the string only
appears once the value has genuinely been read back, which on Linux means the
next launch.

Each accessor names its type, and each falls back to the caller's default rather
than raising: a config someone has hand-edited, or one written by a newer build,
must never stop the app from starting.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, QSettings

from dbkai import APP_ID

# Organization and application are both APP_ID: there is no separate
# organization, and Qt nests the two, which is why the file lands at
# dbkai/dbkai.conf rather than under a vendor directory.
_DEFAULT_STORE = (APP_ID, APP_ID)

#: Which store :func:`settings` opens. Only :func:`use_store` changes it.
_store: tuple[str, str] = _DEFAULT_STORE


def settings() -> QSettings:
    """The application's preference store."""
    return QSettings(*_store)


def use_store(organization: str, application: str) -> None:
    """Point every later :func:`settings` call at a different store.

    **For tests.** Without it a test run works on the developer's own
    preferences: it reads their theme back into the tests and then empties the
    store, so running the suite silently resets the app.

    It redirects *which* store rather than what kind. ``QSettings(organization,
    application)`` hardcodes ``NativeFormat`` in Qt 6 -- ``setDefaultFormat``
    and ``setPath`` do not reach it. A different organization and application
    keeps the backend identical: a different file on Linux and macOS, a
    different key under ``HKCU\\Software`` on Windows.
    """
    global _store
    _store = (organization, application)


def use_default_store() -> None:
    """Go back to the real preference store."""
    global _store
    _store = _DEFAULT_STORE


def load_enum_setting[E: Enum](key: str, default: E) -> E:
    """Read ``key`` as a member of ``default``'s enum, falling back to it.

    Enums are persisted by ``value`` (a stable string) rather than by name or
    ordinal, so renaming a member or reordering the class does not silently
    re-point a stored preference at a different setting.
    """
    stored = settings().value(key)
    if stored is None:
        return default
    try:
        return type(default)(stored)
    except ValueError:
        return default


def save_enum_setting(key: str, value: Enum) -> None:
    """Persist ``value`` under ``key``, by its stable ``value`` string."""
    settings().setValue(key, value.value)


#: The words Qt writes a bool as, plus the digits a hand-edited config is likely
#: to use for them. Anything else is not a bool and falls back to the default.
_BOOLS = {"true": True, "false": False, "1": True, "0": False}


def load_bool_setting(key: str, default: bool) -> bool:
    """Read ``key`` as a bool, falling back to ``default``.

    Accepts the stored text as readily as the bool itself, because which of the
    two comes back depends on whether the value has been round-tripped through
    the store yet - see the module docstring.
    """
    stored = settings().value(key)
    if isinstance(stored, bool):
        return stored
    if stored is None:
        return default
    return _BOOLS.get(str(stored).strip().lower(), default)


def save_bool_setting(key: str, value: bool) -> None:
    """Persist ``value`` under ``key``."""
    settings().setValue(key, value)


def load_int_setting(key: str, default: int) -> int:
    """Read ``key`` as an int, falling back to ``default``.

    The range is not checked here - what counts as a usable number is the
    caller's question.
    """
    stored = settings().value(key)
    if stored is None:
        return default
    try:
        return int(stored)
    except (TypeError, ValueError):
        return default


def save_int_setting(key: str, value: int) -> None:
    """Persist ``value`` under ``key``."""
    settings().setValue(key, value)


def load_bytes_setting(key: str) -> QByteArray | None:
    """Read ``key`` as an opaque blob, or ``None`` if there is no usable one.

    No default: the only things stored this way are Qt's own window geometry
    and dock layout, and a caller with nothing to restore has to lay itself out
    from scratch. Anything stored under the key that is not a blob is treated as
    absent, so a hand-edited config cannot hand ``restoreState`` something it
    will refuse.
    """
    stored = settings().value(key)
    if isinstance(stored, QByteArray):
        return stored
    if isinstance(stored, (bytes, bytearray)):
        return QByteArray(bytes(stored))
    return None


def save_bytes_setting(key: str, value: QByteArray) -> None:
    """Persist ``value`` under ``key``."""
    settings().setValue(key, value)


def load_str_setting(key: str, default: str = "") -> str:
    """Read ``key`` as text, falling back to ``default``.

    Text is what the store keeps, but not always what it hands back: an INI
    value with an unquoted comma - a hand-edited path such as ``C:/DB, Kai`` -
    reads back as a list of strings, which is joined again here. Qt quotes such
    a value when it writes one itself. And reading a preference through
    ``settings().value()`` is the habit this module is here to prevent, so text
    gets an accessor too.
    """
    stored = settings().value(key)
    if stored is None:
        return default
    if isinstance(stored, list):
        return ", ".join(str(part) for part in stored)
    return str(stored)


def save_str_setting(key: str, value: str) -> None:
    """Persist ``value`` under ``key``."""
    settings().setValue(key, value)
