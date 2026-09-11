# Changelog

## Unreleased

- Actions tab: plays an action from the character's `.dsa` files, driving
  the clip, the frame and the part masks from the action's own commands
- Fix: binding a motion that is not one of the model's own choices (opened
  from disk, or by an action) crashed the Animation tab on the next change
- Windows: `uv sync` after pulling; numpy and PyOpenGL are new dependencies

## v0.1.1 - 2026-09-10

- Initial release
- Opens a DB Kai: Ultimate Butoden ROM and browses its models in an OpenGL
  viewport, posed by their body type's motion set
- Parts, animation, material and skeleton panels
- File > Export writes glTF 2.0 with skeleton, textures and animation clips
