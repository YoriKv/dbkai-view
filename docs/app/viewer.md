# The viewer

`app/dbkai/ui/`. One :class:`Session` (`session.py`) holds what is open — the
ROM, the model, the motion bound to it, the frame, the visibility masks and
the view options — and announces changes with Qt signals. Every widget reads
from the session and calls its methods; widgets never talk to each other, so
a panel can be added or removed without touching the others.

## Layout

- **Assets** dock (`asset_tree.py`): every model, motion set and texture set
  in the ROM as a tree of paths with a filter box, including the models and
  motions embedded in the `sp/*.dsdz` story packages. Activating one loads
  it.
- **Viewport** (`viewport.py`): a `QOpenGLWidget` on a 3.3 core context
  through PyOpenGL. Orbit with the left button, pan with the right or middle,
  zoom with the wheel; *View > Reset camera* refits. Its backing and grid are
  literal colours, not palette roles (see [theme.md](theme.md)).
- **Model** dock, four tabs (`panels.py`): *Parts*, *Animation*, *Materials*,
  *Skeleton*.

## Drawing what the game draws

The viewport mirrors the game's own rendering rules
([formats/dse.md](../formats/dse.md), [formats/gx.md](../formats/gx.md)):
unlit, texture modulated by vertex colour, back-face culling unless the mesh
is double-sided, mesh alpha as blending, colour 0 of a texture transparent
when the texture says so, and the mesh-table colour on meshes without vertex
colours. Skinning runs on the CPU in numpy each time the pose changes and the
positions are re-uploaded; models are a few thousand vertices, so this is
free.

**Parts.** The game shows a mesh only when its *group* bit and its material's
*part* bit are both set in a 32-bit mask it drives from game state (facial
expression, hand pose, costume). The Parts tab exposes both masks as
checkboxes, plus a per-mesh override. *Game default* restores the session's
guess at the rest state: every group except the fist, grip and special hand
poses (6, 7, 10-13), and parts 0, 1, 5 and 15. What groups above 13 mean
differs per character (Goku: an alternative hairstyle; Piccolo: the arm with
and without its armour), so they start on and overlap until one is turned
off. *Show all* turns everything on.

**Animation.** A character model automatically binds the motion set of its
body type; a prop that carries its own frames plays those. The tab lists the
clips; the slider scrubs, *Play* runs at the game's 60 frames per second
times the speed. Any other motion file can be opened from disk and bound by
bone name. Billboard bones (the `BL_` hair pieces) face the camera as they do
in the game.

**Materials.** Textures with their decoded preview, and the palette spinner
for textures that ship several (the game's alternate colour schemes).

## Exporting

*File > Export glTF* writes the visible meshes as a `.glb` with the skeleton,
the textures and either the current clip or every clip of the bound motion.
*Export Textures* writes every texture (every palette) as PNG. *Extract
Everything* runs the command-line extractor over the whole ROM into a folder.
The same operations exist headless: `python -m dbkai.cli --help`.

## Checking rendering from a script

Setting `DBKAI_SCREENSHOT=<png>` makes the app load `DBKAI_ASSET` (an asset
path as the Assets dock shows it) after opening the ROM, grab the viewport
two seconds later, write the image and quit. `DBKAI_FRAME` picks a frame of
the default clip. This is how rendering changes are verified without a person
at the screen.
