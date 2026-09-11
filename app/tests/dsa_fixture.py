"""A synthetic DSA action file: two actions, a constant visibility command, a
keyframed one, a colour command and a link, laid out as docs/formats/dsa.md
describes."""

from __future__ import annotations

import struct


def track(keys: list[tuple[int, int]], loop: bool = False, period: int = 0) -> bytes:
    flags = 1 | (2 if loop else 0)  # byte keys
    out = struct.pack("<BBh", flags, len(keys), period)
    out += b"".join(struct.pack("<H", v) for _k, v in keys)
    out += bytes(k for k, _v in keys)
    while len(out) % 4:
        out += b"\0"
    return out


def command(
    op: int, start: int, duration: int, nxt: int, payload: bytes, flags: int = 0x63
) -> bytes:
    head = struct.pack(
        "<BBBBHhhhhh", op, flags, 0, 0xFF, 3, start, duration, -1, -1, nxt
    )
    body = head + payload
    while len(body) % 4:
        body += b"\0"
    return body


def build_actions() -> bytes:
    # Records, in the order the table lists them: 2 actions, then commands.
    tracks_blob = b""
    groups_track = track([(0, 0x00FF), (4, 0x033F)])
    parts_track = track([(0, 0x8043), (4, 0x8023)])
    # Command payloads start at record offset 0x10.
    vis_const = struct.pack("<IBBBBIII", 0, 1, 0, 0, 0, 0x8023033F, 0, 0)
    color = struct.pack("<IB", 0, 2) + bytes(3)
    link = struct.pack("<IHh", 0, 0, 1)
    records: list[bytes] = []
    # action 0: id 1000, 30 frames, first command = record 2
    records.append(
        struct.pack("<BBBBHhhhhhII", 0, 0x63, 0, 0xFF, 3, 0, 30, -1, -1, 2, 0, 1000)
    )
    # action 1: id 2000, 12 frames, first command = record 5
    records.append(
        struct.pack("<BBBBHhhhhhII", 0, 0x63, 0, 0xFF, 3, 0, 12, -1, -1, 5, 0, 2000)
    )
    # record 2: visibility constant for the whole action -> record 3
    records.append(command(0x12, 0, 0, 3, vis_const))
    # record 3: colour scheme 2 from frame 10 -> record 4
    records.append(command(0x13, 10, 0, 4, color))
    # record 4: link to action 1 at frame 20, end of chain
    records.append(command(0x03, 20, 5, 0, link))
    # record 5: keyframed visibility from frame 2 for 8 frames; tracks follow
    #           the records, so their offsets are filled in below.
    records.append(None)  # type: ignore[arg-type]
    fixed = b"".join(r for r in records if r is not None)
    # The tracked command is 0x24 bytes; tracks come after it.
    vis_tracked_len = 0x24
    tracks_at = len(fixed) + vis_tracked_len
    vis_tracked = struct.pack(
        "<IBBBBIII", 0, 2, 0, 0, 0, 0, tracks_at, tracks_at + len(groups_track)
    )
    records[5] = command(0x12, 2, 8, 0, vis_tracked)
    assert len(records[5]) == vis_tracked_len
    tracks_blob = groups_track + parts_track
    blob = b"".join(records) + tracks_blob
    offsets = []
    pos = 0
    for r in records:
        offsets.append(pos)
        pos += len(r)
    n_actions, n_records = 2, len(records)
    resources = struct.pack("<HHiIiI", 0xFFFF, 0xFFFF, 10, 100000, -1, 0)
    resources += struct.pack("<HHiIiI", 0xFFFF, 0xFFFF, -500000, 0, len(blob), 1)
    extra = b""
    sets = struct.pack("<I", 100000)
    header_size = 0x24
    tables = b"".join(struct.pack("<I", o) for o in offsets) + resources + extra + sets
    commands_off = header_size + len(tables)
    header = struct.pack(
        "<4sBBH6HIIII",
        b"DSA\0",
        1,
        0x11,
        header_size,
        n_actions,
        n_records,
        2,
        0,
        1,
        0,
        commands_off,
        commands_off + len(blob),
        0,
        0x400,
    )
    return header + tables + blob + bytes(16)


def build_presets() -> bytes:
    """The visibility preset table: (state id, mask, -1, -1) records."""
    import struct as _struct

    rows = [(10000, 0x802300FF), (10005, 0x8023033F), (11000, 0x804300FF)]
    header = _struct.pack(
        "<4sHHBBBBIHHH",
        b"PRM\0",
        0x7755,
        0x1000,
        6,
        0,
        0,
        1,
        0xFFFFFFFF,
        len(rows),
        0x20,
        0x10,
    )
    header += b"\xff" * (0x20 - len(header))
    body = b"".join(_struct.pack("<IIii", s, m, -1, -1) for s, m in rows)
    return header + body
