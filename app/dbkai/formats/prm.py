"""``PRM`` parameter tables: fixed-size records behind a small header.

The archive's ``/gamedata/`` directories hold these. Only the container is
decoded here; what a table's records mean is up to the caller. One table is
known: the **visibility presets** (archive entry id 64419, in
``/gamedata/parameter``), which map a state id to the draw mask the
character wears in that state (``docs/formats/dsa.md``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"PRM\0"
_HEADER = struct.Struct("<4sHHBBBBIHHH")

#: The archive entry id of the visibility preset table.
VISIBILITY_PRESETS_ID = 64419

#: The preset the viewer rests on: neutral face, open hands. Its value in the
#: shipped table is ``0x8023033F``.
REST_PRESET = 10005


class PrmError(ValueError):
    pass


@dataclass(frozen=True)
class PrmTable:
    records: tuple[bytes, ...]
    record_size: int
    kind: tuple[int, int, int, int]


def is_prm(data: bytes) -> bool:
    return len(data) >= _HEADER.size and data[:4] == MAGIC


def parse(data: bytes) -> PrmTable:
    if not is_prm(data):
        raise PrmError("not a PRM table")
    _magic, _v, _w, k0, k1, k2, k3, _ff, count, header_size, record_size = (
        _HEADER.unpack_from(data)
    )
    if record_size == 0 or header_size + count * record_size > len(data):
        raise PrmError("PRM table does not fit its data")
    records = tuple(
        data[header_size + record_size * i : header_size + record_size * (i + 1)]
        for i in range(count)
    )
    return PrmTable(records, record_size, (k0, k1, k2, k3))


def visibility_presets(table: PrmTable) -> dict[int, int]:
    """State id -> draw mask, from the preset table's ``(id, mask, -1, -1)``
    records."""
    if table.record_size != 16:
        raise PrmError("not the visibility preset table")
    out: dict[int, int] = {}
    for r in table.records:
        state, mask = struct.unpack_from("<II", r)
        out[state] = mask
    return out
