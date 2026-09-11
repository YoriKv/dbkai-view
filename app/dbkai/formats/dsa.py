"""``DSA`` action sets: what a character does frame by frame.

Each ``dsa/<id>_<name>.dsa`` file is a set of **actions** (idle, dash, each
attack, damage reactions, ...). An action is a duration plus a chain of timed
**commands** - hit boxes, sounds, effects, links to other actions, and the
two that matter to a viewer: which parts of the model are shown
(:class:`VisibilityCommand`) and which colour scheme is used
(:class:`ColorCommand`). The layout was read off the game's loader and
interpreter (see ``docs/formats/dsa.md``).

Offsets inside the file are little-endian; every record offset in the tables
is relative to the command area at header offset ``0x14``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

MAGIC = b"DSA\0"

_HEADER = struct.Struct("<4sBBH6HIIII")
_RESOURCE = struct.Struct("<HHiIiI")


class DsaError(ValueError):
    """The bytes are not an action set this parser understands."""


class Op(IntEnum):
    """Command opcodes. Names are given only to the ones whose handlers have
    been read; the rest keep their number."""

    ACTION = 0x00  # the action record itself heads its own chain
    LINK = 0x03  # branch to another action
    HIT = 0x04
    SOUND = 0x09  # play a sound: kind and sequence id, not a resource
    MOTION = 0x11  # play motion clips: a looping list of segments
    VISIBILITY = 0x12
    COLOR = 0x13
    HIT_GROUP = 0x14


@dataclass(frozen=True)
class Track:
    """A keyframed 16-bit value: ``keys`` are (frame, value) pairs. Before the
    first key the value is ``0xFFFF``; after a key it holds until the next.
    ``period`` > 0 makes the frame wrap when the ``loop`` flag is set."""

    keys: tuple[tuple[int, int], ...]
    loop: bool
    period: int

    def value_at(self, frame: int) -> int:
        if not self.keys:
            return 0
        if self.loop and self.period > 0 and frame >= self.period:
            frame %= self.period
        value = 0xFFFF
        for key, v in self.keys:
            if key > frame:
                break
            value = v
        return value


@dataclass(frozen=True)
class Command:
    """One timed command of an action.

    ``start`` and ``duration`` are in frames of the action; a duration of 0
    means "until the action ends". ``condition`` gates the command on a
    character state value when ``conditional`` is set.
    """

    index: int
    op: int
    flags: int
    condition: int
    start: int
    duration: int
    payload: bytes = field(repr=False)

    @property
    def active(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def conditional(self) -> bool:
        return bool(self.flags & 0x80)

    def covers(self, frame: int) -> bool:
        if frame < self.start:
            return False
        return self.duration <= 0 or frame < self.start + self.duration


@dataclass(frozen=True)
class VisibilityCommand(Command):
    """Op ``0x12``: sets the draw mask - groups in the low 16 bits, material
    parts in the high 16 - either to a constant or from two tracks."""

    constant: int = 0
    groups_track: Track | None = None
    parts_track: Track | None = None

    def mask_at(self, frame: int) -> int:
        """The mask at ``frame`` of the action (0xFFFF halves mean "before
        the track's first key", which the game treats as all shown)."""
        if self.groups_track is None or self.parts_track is None:
            return self.constant
        rel = frame - self.start
        return self.groups_track.value_at(rel) | (self.parts_track.value_at(rel) << 16)


@dataclass(frozen=True)
class ColorCommand(Command):
    """Op ``0x13``: selects the model's colour scheme (texture palette)."""

    scheme: int = 0


@dataclass(frozen=True)
class LinkCommand(Command):
    """Op ``0x03``: at ``start`` the character may branch to another action."""

    target: int = 0


@dataclass(frozen=True)
class SoundCommand(Command):
    """Op ``0x09``: plays a sound of ``kind`` (voice, effect group) with the
    given sequence id."""

    kind: int = 0
    sound: int = 0


@dataclass(frozen=True)
class Segment:
    """One piece of a motion command: play ``resource`` (a clip of a motion
    set) from take frame ``start`` for ``length`` frames."""

    resource: int
    start: int
    length: int
    flag: int


@dataclass(frozen=True)
class MotionCommand(Command):
    """Op ``0x11``: the clips the character plays while the command covers
    the action's frames. Segments run back to back; ``period`` > 0 wraps
    the elapsed frames so the list loops."""

    segments: tuple[Segment, ...] = ()
    period: int = 0
    mode: int = 0

    def segment_at(self, frame: int) -> tuple[Segment, int] | None:
        """The segment covering action ``frame`` and the take frame within its
        clip, as the game's evaluator computes them."""
        rel = frame - self.start
        if rel < 0:
            return None
        if self.period > 0:
            rel %= self.period
        for seg in self.segments:
            if seg.resource < 0:
                continue
            if rel < seg.length:
                return seg, seg.start + rel
            rel -= seg.length
        return None


@dataclass(frozen=True)
class Resource:
    """An entry of the resource table: a motion clip of a motion set (``set_id``
    such as 100000 for the NORMAL body, ``number`` the clip's leading number),
    or data embedded in this file at ``offset``."""

    index: int
    number: int
    set_id: int
    offset: int
    flags: int

    @property
    def embedded(self) -> bool:
        return self.offset >= 0


@dataclass(frozen=True)
class Action:
    index: int
    action_id: int
    duration: int
    commands: tuple[Command, ...]

    @property
    def visibility(self) -> list[VisibilityCommand]:
        return [c for c in self.commands if isinstance(c, VisibilityCommand)]

    @property
    def colors(self) -> list[ColorCommand]:
        return [c for c in self.commands if isinstance(c, ColorCommand)]

    @property
    def motions(self) -> list[MotionCommand]:
        return [c for c in self.commands if isinstance(c, MotionCommand)]

    def motion_at(self, frame: int) -> tuple[int, int] | None:
        """(resource index, take frame) of the clip playing at ``frame``, or
        ``None`` when no motion command covers it."""
        best: MotionCommand | None = None
        for c in self.motions:
            if c.active and not c.conditional and c.covers(frame):
                if best is None or c.start >= best.start:
                    best = c
        if best is None:
            return None
        found = best.segment_at(frame)
        return None if found is None else (found[0].resource, found[1])

    def mask_at(self, frame: int) -> int | None:
        """The draw mask in force at ``frame``: the latest visibility command
        covering it, or ``None`` when none does (the mask is then whatever
        the previous action left)."""
        best: VisibilityCommand | None = None
        for c in self.visibility:
            if c.active and not c.conditional and c.covers(frame):
                if best is None or c.start >= best.start:
                    best = c
        return None if best is None else best.mask_at(frame)

    def scheme_at(self, frame: int) -> int | None:
        best: ColorCommand | None = None
        for c in self.colors:
            if c.active and not c.conditional and c.covers(frame):
                if best is None or c.start >= best.start:
                    best = c
        return None if best is None else best.scheme


@dataclass
class ActionSet:
    name: str
    actions: list[Action]
    resources: list[Resource]
    motion_sets: list[int]
    raw_kind: int = 0

    def action_by_id(self, action_id: int) -> Action | None:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        return None


def is_dsa(data: bytes) -> bool:
    return len(data) >= _HEADER.size and data[:4] == MAGIC


def split_mask(mask: int) -> tuple[set[int], set[int]]:
    """A draw mask as the (groups, parts) it enables."""
    groups = {g for g in range(16) if mask & (1 << g)}
    parts = {p for p in range(16) if mask & (1 << (16 + p))}
    return groups, parts


def join_mask(groups: set[int], parts: set[int]) -> int:
    mask = 0
    for g in groups:
        mask |= 1 << g
    for p in parts:
        mask |= 1 << (16 + p)
    return mask


def _track(data: bytes, offset: int) -> Track | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    flags, count = data[offset], data[offset + 1]
    period = struct.unpack_from("<h", data, offset + 2)[0]
    values = struct.unpack_from(f"<{count}H", data, offset + 4)
    keys_at = offset + 4 + 2 * count
    if flags & 1:
        keys = struct.unpack_from(f"<{count}B", data, keys_at)
    else:
        keys = struct.unpack_from(f"<{count}H", data, keys_at)
    return Track(tuple(zip(keys, values, strict=True)), bool(flags & 2), period)


def parse(data: bytes, name: str = "") -> ActionSet:
    if not is_dsa(data):
        raise DsaError("not a DSA file")
    (
        _magic,
        raw_kind,
        flags7,
        header_size,
        n_actions,
        n_records,
        n_resources,
        n_extra,
        n_sets,
        _pad,
        commands_off,
        tail_off,
        _z,
        _x400,
    ) = _HEADER.unpack_from(data)
    if header_size != _HEADER.size:
        raise DsaError(f"unexpected header size {header_size:#x}")
    if n_records < n_actions or commands_off > len(data):
        raise DsaError("corrupt action set header")
    t1 = header_size
    table = struct.unpack_from(f"<{n_records}I", data, t1)
    t3 = t1 + 4 * n_records
    t4 = t3 + _RESOURCE.size * n_resources
    t5 = t4 + 12 * n_extra
    resources = []
    for i in range(n_resources):
        _a, _b, number, set_id, offset, rflags = _RESOURCE.unpack_from(
            data, t3 + _RESOURCE.size * i
        )
        resources.append(Resource(i, number, set_id, offset, rflags))
    motion_sets = list(struct.unpack_from(f"<{n_sets}I", data, t5))
    base = commands_off

    def record(index: int) -> tuple[int, int, int, int, int, int, int]:
        p = base + table[index]
        if p + 16 > len(data):
            raise DsaError(f"record {index} is outside the file")
        op, fl, cond, _b3, _u4, start, dur, _a, _c, nxt = struct.unpack_from(
            "<BBBBHhhhhh", data, p
        )
        return p, op, fl, cond, start, dur, nxt

    def record_end(index: int) -> int:
        later = [o for o in table if o > table[index]]
        return base + (
            min(later)
            if later
            else (tail_off - base if tail_off > base else len(data) - base)
        )

    def build(index: int) -> Command:
        p, op, fl, cond, start, dur, nxt = record(index)
        payload = data[p + 16 : record_end(index)]
        common = dict(
            index=index,
            op=op,
            flags=fl,
            condition=cond,
            start=start,
            duration=dur,
            payload=payload,
        )
        if op == Op.VISIBILITY and len(payload) >= 0x14:
            mode = payload[4]
            constant = struct.unpack_from("<I", payload, 8)[0]
            groups_off, parts_off = struct.unpack_from("<II", payload, 12)
            if mode == 2:
                return VisibilityCommand(
                    **common,
                    constant=constant,
                    groups_track=_track(data, base + groups_off),
                    parts_track=_track(data, base + parts_off),
                )
            return VisibilityCommand(**common, constant=constant)
        if op == Op.COLOR and len(payload) >= 5:
            return ColorCommand(**common, scheme=payload[4])
        if op == Op.LINK and len(payload) >= 8:
            return LinkCommand(**common, target=struct.unpack_from("<h", payload, 6)[0])
        if op == Op.SOUND and len(payload) >= 4:
            sound, kind = struct.unpack_from("<hh", payload, 0)
            return SoundCommand(**common, kind=kind, sound=sound)
        if op == Op.MOTION and len(payload) >= 0x10:
            _data_off, mode, count, _u, _hit, period, seg_off = struct.unpack_from(
                "<IBBHhhI", payload, 0
            )
            segments = []
            for k in range(count):
                at = base + seg_off + 8 * k
                if at + 8 > len(data):
                    break
                segments.append(Segment(*struct.unpack_from("<4h", data, at)))
            return MotionCommand(
                **common, segments=tuple(segments), period=period, mode=mode
            )
        return Command(**common)

    actions = []
    for a in range(n_actions):
        p, op, _fl, _cond, _start, dur, nxt = record(a)
        action_id = (
            struct.unpack_from("<I", data, p + 0x14)[0] if p + 0x18 <= len(data) else 0
        )
        chain: list[Command] = []
        seen: set[int] = set()
        while nxt > 0 and nxt < n_records and nxt not in seen:
            seen.add(nxt)
            cmd = build(nxt)
            chain.append(cmd)
            nxt = record(nxt)[6]
        actions.append(Action(a, action_id, dur, tuple(chain)))
    return ActionSet(name, actions, resources, motion_sets, raw_kind | (flags7 << 8))
