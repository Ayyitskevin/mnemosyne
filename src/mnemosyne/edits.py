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


def set_hero(
    conn: sqlite3.Connection, album_id: int, spread_id: int, photo_id: int
) -> bool:
    """Make `photo_id` the hero of its spread. Returns False unless the photo is
    actually placed on that spread (so a stray id can't crown a photo that isn't
    even there). The layout is recomputed from hero_photo_id on every render, so
    this single update is all that changes — the new hero takes the dominant slot
    and may flip the spread to a different template if its orientation differs."""
    placed = conn.execute(
        "SELECT 1 FROM spreads s JOIN placements pl ON pl.spread_id = s.id "
        "WHERE s.id = ? AND s.album_id = ? AND pl.photo_id = ?",
        (spread_id, album_id, photo_id),
    ).fetchone()
    if placed is None:
        return False
    conn.execute(
        "UPDATE spreads SET hero_photo_id = ? WHERE id = ?", (photo_id, spread_id)
    )
    conn.commit()
    return True


def move_photo(
    conn: sqlite3.Connection,
    album_id: int,
    spread_id: int,
    photo_id: int,
    direction: str,
) -> bool:
    """Swap a photo with its neighbour in slot order within one spread ('up' =
    earlier slot, 'down' = later). The hero always takes the dominant area
    regardless of slot, so this only reshuffles the supporting photos' fill order
    (b, c, d). Returns False at the ends or if the photo isn't on the spread."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    op, order = _DIRECTIONS[direction]

    if conn.execute(
        "SELECT 1 FROM spreads WHERE id = ? AND album_id = ?", (spread_id, album_id)
    ).fetchone() is None:
        return False

    row = conn.execute(
        "SELECT id, slot FROM placements WHERE spread_id = ? AND photo_id = ?",
        (spread_id, photo_id),
    ).fetchone()
    if row is None:
        return False
    slot, placement_id = row["slot"], row["id"]

    neighbour = conn.execute(
        f"SELECT id, slot FROM placements WHERE spread_id = ? AND slot {op} ? "
        f"ORDER BY slot {order} LIMIT 1",
        (spread_id, slot),
    ).fetchone()
    if neighbour is None:
        return False

    conn.execute(
        "UPDATE placements SET slot = ? WHERE id = ?", (neighbour["slot"], placement_id)
    )
    conn.execute(
        "UPDATE placements SET slot = ? WHERE id = ?", (slot, neighbour["id"])
    )
    conn.commit()
    return True
