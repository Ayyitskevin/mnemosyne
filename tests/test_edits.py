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
    # Three photos on spread 10 (slots 1..3), hero is photo 100; a stray photo 200
    # belongs to a different spread so it can stand in for "not on this spread".
    for pid in (100, 101, 102, 200):
        c.execute(
            "INSERT INTO photos (id, album_id, storage_key, width, height) "
            "VALUES (?, 1, ?, 1200, 800)",
            (pid, f"a1/{pid}.jpg"),
        )
    for slot, pid in enumerate((100, 101, 102), start=1):
        c.execute(
            "INSERT INTO placements (spread_id, photo_id, slot) VALUES (10, ?, ?)",
            (pid, slot),
        )
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (11, 200, 1)")
    c.execute("UPDATE spreads SET hero_photo_id = 100 WHERE id = 10")
    c.commit()
    return c


def _order(c: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in c.execute(
        "SELECT id FROM spreads WHERE album_id = 1 ORDER BY position"
    )]


def _slots(c: sqlite3.Connection, spread_id: int) -> list[int]:
    return [r["photo_id"] for r in c.execute(
        "SELECT photo_id FROM placements WHERE spread_id = ? ORDER BY slot", (spread_id,)
    )]


def _hero(c: sqlite3.Connection, spread_id: int) -> int:
    return c.execute(
        "SELECT hero_photo_id FROM spreads WHERE id = ?", (spread_id,)
    ).fetchone()["hero_photo_id"]


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


def test_set_hero_to_another_photo_on_the_spread(conn):
    assert edits.set_hero(conn, 1, 10, 102) is True
    assert _hero(conn, 10) == 102


def test_set_hero_rejects_photo_not_on_the_spread(conn):
    # Photo 200 is on spread 11, not 10 — it must not be crownable here.
    assert edits.set_hero(conn, 1, 10, 200) is False
    assert _hero(conn, 10) == 100  # unchanged


def test_move_photo_down_swaps_slot_with_next(conn):
    assert edits.move_photo(conn, 1, 10, 100, "down") is True
    assert _slots(conn, 10) == [101, 100, 102]


def test_move_photo_up_swaps_slot_with_previous(conn):
    assert edits.move_photo(conn, 1, 10, 102, "up") is True
    assert _slots(conn, 10) == [100, 102, 101]


def test_move_photo_at_edge_is_a_noop(conn):
    assert edits.move_photo(conn, 1, 10, 100, "up") is False
    assert _slots(conn, 10) == [100, 101, 102]


def test_move_photo_not_on_spread_changes_nothing(conn):
    assert edits.move_photo(conn, 1, 10, 200, "down") is False
    assert _slots(conn, 10) == [100, 101, 102]


def test_move_photo_bad_direction_raises(conn):
    with pytest.raises(ValueError):
        edits.move_photo(conn, 1, 10, 101, "sideways")
