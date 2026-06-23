"""Tests for spread reordering — the user's nudge to the AI's draft. These encode
why the swap matters: moving a spread up must trade places with exactly its
neighbour (not jump or renumber the whole album), boundaries must no-op rather
than corrupt order, and a bogus spread id must not silently shuffle anything."""
from __future__ import annotations

import sqlite3

import pytest

from mnemosyne import db, edits


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    c.execute("INSERT INTO albums (id, name, source_dir) VALUES (1, 'a', '/x')")
    for sid, pos in [(10, 1), (11, 2), (12, 3)]:
        c.execute(
            "INSERT INTO spreads (id, album_id, position) VALUES (?, 1, ?)", (sid, pos)
        )
    c.commit()
    return c


def _order(c: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in c.execute(
        "SELECT id FROM spreads WHERE album_id = 1 ORDER BY position"
    )]


def test_move_up_swaps_with_previous_neighbour(conn):
    assert edits.move_spread(conn, 1, 12, "up") is True
    assert _order(conn) == [10, 12, 11]


def test_move_down_swaps_with_next_neighbour(conn):
    assert edits.move_spread(conn, 1, 10, "down") is True
    assert _order(conn) == [11, 10, 12]


def test_move_up_at_top_is_a_noop(conn):
    assert edits.move_spread(conn, 1, 10, "up") is False
    assert _order(conn) == [10, 11, 12]


def test_move_down_at_bottom_is_a_noop(conn):
    assert edits.move_spread(conn, 1, 12, "down") is False
    assert _order(conn) == [10, 11, 12]


def test_unknown_spread_changes_nothing(conn):
    assert edits.move_spread(conn, 1, 999, "up") is False
    assert _order(conn) == [10, 11, 12]


def test_bad_direction_raises(conn):
    with pytest.raises(ValueError):
        edits.move_spread(conn, 1, 11, "sideways")
