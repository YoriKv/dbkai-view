"""The tabs of the Model dock: parts, animation, actions, materials and
skeleton, one module each.

Each panel is a widget over the :class:`~dbkai.ui.session.Session`. They
rebuild themselves when the session says the model, motion or action sets
changed and push the user's choices back through its methods.
"""

from dbkai.ui.panels.actions import ActionsPanel
from dbkai.ui.panels.animation import AnimationPanel
from dbkai.ui.panels.materials import MaterialsPanel
from dbkai.ui.panels.parts import PartsPanel
from dbkai.ui.panels.skeleton import SkeletonPanel

__all__ = [
    "ActionsPanel",
    "AnimationPanel",
    "MaterialsPanel",
    "PartsPanel",
    "SkeletonPanel",
]
