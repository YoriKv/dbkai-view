"""The preference store's typed accessors.

These guard the one thing about QSettings that does not announce itself: it
stores text, so a value only stops being the Python object that was written
once it has genuinely been read back. Within a process Qt answers from its
config cache, so the reads below are tested against a file this process never
wrote -- the only way to see in-process what the user sees next launch.
"""

import conftest
from PySide6.QtCore import QByteArray, QSettings

from dbkai import APP_ID
from dbkai.ui import settings as settings_module
from dbkai.ui.settings import (
    load_bool_setting,
    load_bytes_setting,
    load_int_setting,
    save_bool_setting,
    save_bytes_setting,
    settings,
)

FLAG = "test/flag"
COUNT = "test/count"
BLOB = "test/blob"


def from_file(tmp_path, monkeypatch, text: str) -> None:
    """Point the accessors at an INI file written here as plain text."""
    ini = tmp_path / "prefs.ini"
    ini.write_text(text)
    store = QSettings(str(ini), QSettings.Format.IniFormat)
    monkeypatch.setattr(settings_module, "settings", lambda: store)


def test_a_bool_read_back_from_the_file_is_a_bool(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\nflag=true\n")

    value = load_bool_setting(FLAG, False)
    assert value is True


def test_a_bool_written_off_is_read_off(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\nflag=false\n")

    assert load_bool_setting(FLAG, True) is False


def test_a_bool_survives_the_store_the_app_uses():
    save_bool_setting(FLAG, True)
    settings().sync()

    assert load_bool_setting(FLAG, False) is True


def test_a_nonsense_bool_falls_back(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\nflag=banana\n")

    assert load_bool_setting(FLAG, False) is False
    assert load_bool_setting(FLAG, True) is True


def test_an_int_read_back_from_the_file_is_an_int(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\ncount=50\n")

    assert load_int_setting(COUNT, 0) == 50


def test_a_nonsense_int_falls_back(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\ncount=half\n")

    assert load_int_setting(COUNT, 7) == 7


def test_a_blob_survives_the_store_the_app_uses():
    save_bytes_setting(BLOB, QByteArray(b"\x01\x02\x03"))
    settings().sync()

    assert load_bytes_setting(BLOB) == QByteArray(b"\x01\x02\x03")


def test_text_where_a_blob_should_be_is_nothing(tmp_path, monkeypatch):
    from_file(tmp_path, monkeypatch, "[test]\nblob=not a blob\n")

    assert load_bytes_setting(BLOB) is None


def test_the_suite_is_not_working_on_the_real_preference_store():
    store = settings()
    assert (store.organizationName(), store.applicationName()) != (APP_ID, APP_ID)
    assert store.organizationName() == conftest.TEST_STORE[0]
    assert store.applicationName() == conftest.TEST_STORE[1]


def test_the_suite_uses_the_same_kind_of_store_the_app_ships():
    assert settings().format() == QSettings(APP_ID, APP_ID).format()
