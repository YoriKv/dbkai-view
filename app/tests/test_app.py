"""The bootstrap and the window it shows."""

import logging

import pytest

from dbkai import APP_NAME, configure_logging, resources


def test_the_window_is_titled_for_the_app(window) -> None:
    assert window.windowTitle() == APP_NAME


def test_the_app_icon_is_bundled() -> None:
    assert resources.read_bytes("icons", "app.png").startswith(b"\x89PNG")


def test_logging_stays_silent_unless_asked() -> None:
    assert configure_logging("") is None


def test_help_about_returns_under_the_suite(window) -> None:
    # The About box is not a failure report: conftest makes it return rather
    # than turning it into an exception like the other message boxes.
    window.show_about()


def test_the_file_argument_skips_the_program_name_and_options() -> None:
    from dbkai.app import named_file

    assert named_file(["dbkai"]) is None
    assert named_file(["dbkai", "-psn_0_1", "game.nds"]) == "game.nds"


@pytest.mark.parametrize("value", ["0", "false", "OFF", "no"])
def test_logging_stays_off_when_turned_off(value: str) -> None:
    assert configure_logging(value) is None


def test_logging_takes_a_level_or_a_truthy_value() -> None:
    logger = logging.getLogger("dbkai")
    handlers, level = list(logger.handlers), logger.level
    try:
        assert configure_logging("TRUE") == logging.DEBUG
        assert configure_logging("info") == logging.INFO
    finally:
        logger.handlers[:] = handlers
        logger.setLevel(level)
