# Changelog

## Unreleased

- Actions tab: plays an action from the character's `.dsa` files, driving
  the clip, the frame and the part masks from the action's own commands;
  laid out like the Animation tab, with a source chooser and a list, and
  switching actions keeps playing; action files activated in the Assets
  dock join it and can be removed again
- Fix: the colour scheme an action chose stuck to the palette after leaving
  the action or opening another model; it now reverts, and the Materials
  tab's palette spinner follows it
- Fix: activating an action file in the Assets dock raised "not a DSE
  file"; it now joins the Actions tab of the loaded model
- Fix: binding a motion that is not one of the model's own choices (opened
  from disk, or by an action) crashed the Animation tab on the next change
- Windows: `uv sync` after pulling; numpy and PyOpenGL are new dependencies
- Fix: mesh alpha comes from the material record as the game reads it, not
  from the material-select chunk, so the cap, the scouter glass and other
  accessories draw with the right opacity instead of vanishing
- Fix: mesh flag `0x10` culls front faces (the inside of hair pieces) and
  flag `0x04` draws both sides; glTF export rewinds the former
- Fix: motion files named like models (`debug/goku/262_goku_nyoibou.dse`)
  are listed as motions
- A model the character preset would hide entirely (accessories, props)
  shows everything at rest

## v0.1.1 - 2026-09-10

- Initial release
- Opens a DB Kai: Ultimate Butoden ROM and browses its models in an OpenGL
  viewport, posed by their body type's motion set
- Parts, animation, material and skeleton panels
- File > Export writes glTF 2.0 with skeleton, textures and animation clips
