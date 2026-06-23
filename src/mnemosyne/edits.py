"""Edits — user nudges to the AI's draft album.

The arrange station makes the first pass; this is where the photographer overrides
it. Phase-1 nudge: reorder spreads. Kept on the write side (separate from the
read-only albums.py) because these mutate album state.

Note: re-running arrange rebuilds the spreads table from scratch, so a manual
reorder is overwritten by a fresh "redesign" — that's intended (redesign means
start over), but it's why nudges live apart from the generated layout.
"""
from __future__ import annotations

import sqlite3

_DIRECTIONS = {"up": ("<", "DESC"), "down": (">", "ASC")}


def move_spread(
    conn: sqlite3.Connection, album_id: int, spread_id: int, direction: str
) -> bool:
    """Swap a spread with its neighbour in the given direction ('up' = earlier in
    the album, 'down' = later). Returns True if a swap happened, False if the
    spread doesn't exist or is already at that end. No UNIQUE constraint on
    (album_id, position), so the two-row position swap is safe in one transaction."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    op, order = _DIRECTIONS[direction]

    row = conn.execute(
        "SELECT position FROM spreads WHERE id = ? AND album_id = ?",
        (spread_id, album_id),
    ).fetchone()
    if row is None:
        return False
    pos = row["position"]

    neighbour = conn.execute(
        f"SELECT id, position FROM spreads WHERE album_id = ? AND position {op} ? "
        f"ORDER BY position {order} LIMIT 1",
        (album_id, pos),
    ).fetchone()
    if neighbour is None:
        return False  # already at the top/bottom

    conn.execute(
        "UPDATE spreads SET position = ? WHERE id = ?", (neighbour["position"], spread_id)
    )
    conn.execute(
        "UPDATE spreads SET position = ? WHERE id = ?", (pos, neighbour["id"])
    )
    conn.commit()
    return True
