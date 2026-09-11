"""The bootstrap and the window it shows."""

from dbkai import APP_NAME, configure_logging, resources


def test_the_window_is_titled_for_the_app(window) -> None:
    assert window.windowTitle() == APP_NAME


def test_the_app_icon_is_bundled() -> None:
    assert resources.read_bytes("icons", "app.png").startswith(b"\x89PNG")


def test_logging_stays_silent_unless_asked() -> None:
    assert configure_logging("") is None
