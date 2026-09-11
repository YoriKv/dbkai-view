# The theme

`app/dbkai/ui/theme.py`. The app themes through **QPalette**, not a stylesheet:
every widget already draws from palette roles, so handing the application a
different palette re-colours the whole UI without a per-widget rule anywhere.
The only literals should be the ones that are deliberately *not* theme colours
— a model viewport's backing and grid, which have to read the same against the
artwork whichever theme is on.

View > Theme switches it live, and the choice is remembered under
`view/theme` in the preference store.

**Both themes run on Fusion.** The native Windows and macOS styles paint many
controls from platform colours and ignore the application palette, so a dark
palette under them comes out half-light. Fusion honours the palette everywhere
and ships on every platform Qt does; using it for light as well is what makes a
screenshot from one machine describe the app on the others.

**A theme is a data row.** `_PaletteSpec` in `_PALETTES`: a surface colour to
derive the whole palette from, plus the handful of roles whose derived value is
wrong. `QPalette(QColor)` computes window, button, text and the bevel shades
from that one colour, so adding a theme is a row, not a code path. A row that
names **no** surface means the style's own `standardPalette()`, which has to be
asked of the style *about to be installed*; `palette_for` refuses to answer for
such a theme without being handed one.

**Install the palette before the style.** Qt propagates an application palette
through the event loop rather than inside `setPalette`, and installing a style
in between re-polishes every widget against the palette it already has — the
queued `PaletteChange` is then considered satisfied and never delivered, so any
pixmap baked from the palette keeps yesterday's colours.

**Derived colours are a distance from a palette role.** `blended(over, under,
amount)` mixes two roles opaquely; use it rather than a literal for anything
that has to hold up on both surfaces.

**Baked pixmaps go stale.** Anything that bakes a palette colour into a pixmap
re-bakes on `QEvent.Type.PaletteChange`.

`app/tests/test_theme.py` asserts both themes run on Fusion, the light theme is
the style's own palette, and the menu switches and remembers the theme.
