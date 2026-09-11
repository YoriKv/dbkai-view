"""An engine-neutral scene built from the decoded files.

:mod:`dbkai.formats` gives back what is *in* a file; this package turns that
into what the game *draws*: meshes with model-space vertices, a skeleton with
bind and world matrices, animations bound to that skeleton, and the rules the
game applies to decide which parts are visible. Both the Qt viewer and the
exporters consume this, and nothing here imports Qt.
"""
