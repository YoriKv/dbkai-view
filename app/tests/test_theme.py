"""The theme: Fusion for both, a palette per theme, and a live switch."""

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from dbkai.ui.settings import load_enum_setting
from dbkai.ui.theme import THEME_KEY, Theme, apply_theme, palette_for


@pytest.mark.parametrize("theme", list(Theme))
def test_both_themes_are_drawn_in_fusion(qapp, theme: Theme) -> None:
    # Fusion for light as well as dark, so a palette is the whole of what a
    # theme is: the native styles ignore an application palette in half their
    # controls, which is how a "dark" theme comes out half-light on Windows.
    try:
        apply_theme(theme)
        app = QApplication.instance()
        assert app.style().baseStyle().objectName().lower() == "fusion"
        window = app.palette().color(QPalette.ColorRole.Window).lightness()
        assert (window < 128) is (theme is Theme.DARK)
    finally:
        # The QApplication outlives the test; leave it as the rest of the suite
        # expects to find it.
        apply_theme(Theme.LIGHT)


def test_a_theme_that_names_no_surface_wears_the_styles_own_palette(qapp) -> None:
    style = QApplication.instance().style()
    assert palette_for(Theme.LIGHT, style) == style.standardPalette()
    assert palette_for(Theme.DARK, style) != style.standardPalette()


def test_a_style_palette_theme_refuses_to_answer_without_a_style() -> None:
    with pytest.raises(ValueError):
        palette_for(Theme.LIGHT)


def test_the_theme_menu_switches_and_remembers(window) -> None:
    try:
        dark = next(a for a in window.theme.actions() if a.data() is Theme.DARK)
        dark.trigger()
        app = QApplication.instance()
        assert app.palette().color(QPalette.ColorRole.Window).lightness() < 128
        assert load_enum_setting(THEME_KEY, Theme.LIGHT) is Theme.DARK
    finally:
        apply_theme(Theme.LIGHT)
