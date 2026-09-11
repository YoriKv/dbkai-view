"""Actions resolved against the motion sets they name.

An action's motion command plays clips by resource: a motion set id and the
clip's leading number. :func:`action_pose` turns one action frame into the
motion, clip and file frame that pose the character; :func:`action_take`
samples a whole action, poses and draw masks, for export. The viewer and the
exporter share both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dbkai.formats import dsa
from dbkai.model.animation import BoundMotion, Clip, Motion, Take
from dbkai.model.skeleton import Skeleton

#: The motion set with a given id, or ``None`` when the game has none.
MotionSets = Callable[[int], Motion | None]


@dataclass(frozen=True)
class ActionPose:
    motion: Motion
    clip: Clip
    #: The file frame of ``motion``.
    frame: int


def action_pose(
    file: dsa.ActionSet, action: dsa.Action, frame: int, motion_sets: MotionSets
) -> ActionPose | None:
    """What poses the character at action ``frame``, or ``None`` when no
    motion command covers it or its resource does not resolve (embedded
    data, an unknown set, a clip the set lacks)."""
    found = action.motion_at(frame)
    if found is None:
        return None
    resource, take_frame = found
    if not 0 <= resource < len(file.resources):
        return None
    res = file.resources[resource]
    if res.embedded or res.set_id == 0:
        return None
    motion = motion_sets(res.set_id)
    if motion is None:
        return None
    clip = motion.clip_by_number(res.number)
    if clip is None:
        return None
    return ActionPose(motion, clip, clip.frame_for_take(take_frame))


def action_name(file: dsa.ActionSet, action: dsa.Action) -> str:
    """``<file stem>_<action id>``, such as ``100000_NORMAL_BALANCE_1000``."""
    return f"{Path(file.name).stem}_{action.action_id}"


def action_take(
    skeleton: Skeleton,
    file: dsa.ActionSet,
    action: dsa.Action,
    motion_sets: MotionSets,
    base_mask: int,
) -> Take:
    """Every frame of ``action`` as a :class:`Take`.

    A frame no motion covers holds the pose before it, as the viewer does;
    leading ones take the first pose that resolves, and an action that never
    resolves one keeps the bind pose. A frame no visibility command covers
    holds the mask before it, starting from ``base_mask``.
    """
    count = max(action.duration, 1)
    bound: dict[int, BoundMotion] = {}
    poses: list[tuple[BoundMotion, int] | None] = []
    for k in range(count):
        pose = action_pose(file, action, k, motion_sets)
        if pose is None:
            poses.append(poses[-1] if poses else None)
            continue
        b = bound.get(id(pose.motion))
        if b is None:
            b = bound[id(pose.motion)] = BoundMotion.bind(skeleton, pose.motion)
        poses.append((b, pose.frame))
    first = next((p for p in poses if p is not None), None)
    poses = [first if p is None else p for p in poses]
    masks: list[int] = []
    mask = base_mask
    for k in range(count):
        found = action.mask_at(k)
        if found is not None:
            mask = found
        masks.append(mask)
    return Take(action_name(file, action), poses, masks)
