"""Tests for the opt-in deterministic arrange backend (the live engine path).

These encode what makes it safe to flip on for real albums: with
MNEMOSYNE_ARRANGE_BACKEND=deterministic the album is laid out by the curate engine
with NO model call, every photo is placed exactly once, the result is reproducible,
and the proposal's provenance reports the deterministic proposer. The source-verify
guard also reroutes ANY layout (model or engine) that fails to place every photo to
the safe fallback — the never-omit/duplicate guardrail, enforced at the source.
"""
from __future__ import annotations

import sqlite3

import pytest

from mnemosyne import arrange, config, db, proposal, runtime


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    c.execute(
        "INSERT INTO albums (id, name, source_dir, gallery_theme) "
        "VALUES (1, 'g', '/x', 'wedding')"
    )
    # A standout, a couple of strong shots, and filler — with arc scene labels.
    rows = [
        (1, "getting ready detail", 0.30, 0.80),
        (2, "wide ceremony establishing shot", 0.50, 0.70),
        (3, "couple portrait", 0.92, 0.95),   # standout -> cover + solo
        (4, "family group", 0.78, 0.80),      # strong -> anchor
        (5, "reception candids", 0.40, 0.60),
        (6, "first dance", 0.55, 0.65),
        (7, "send-off moment", 0.83, 0.85),
    ]
    for pid, scene, hero, keeper in rows:
        c.execute(
            "INSERT INTO photos (id, album_id, storage_key, width, height, scene, "
            "hero_score, keeper_score) VALUES (?, 1, ?, 1200, 800, ?, ?, ?)",
            (pid, f"a1/{pid}.jpg", scene, hero, keeper),
        )
    c.commit()
    return c


def _placed(conn: sqlite3.Connection) -> list[int]:
    return [
        r["photo_id"]
        for r in conn.execute(
            "SELECT pl.photo_id FROM placements pl JOIN spreads s ON s.id = pl.spread_id "
            "WHERE s.album_id = 1"
        ).fetchall()
    ]


def _structure(conn: sqlite3.Connection) -> list[tuple]:
    out = []
    for s in conn.execute(
        "SELECT id, position, hero_photo_id FROM spreads WHERE album_id = 1 ORDER BY position"
    ).fetchall():
        photos = [
            r["photo_id"]
            for r in conn.execute(
                "SELECT photo_id FROM placements WHERE spread_id = ? ORDER BY slot",
                (s["id"],),
            ).fetchall()
        ]
        out.append((s["position"], s["hero_photo_id"], tuple(photos)))
    return out


def test_deterministic_backend_lays_out_without_a_model(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")

    def _no_model(*a, **kw):
        raise AssertionError("the model must not be called on the deterministic path")

    monkeypatch.setattr(arrange, "_ask_model", _no_model)

    n = arrange.arrange_album(conn, 1)
    assert n > 0
    placed = _placed(conn)
    assert sorted(placed) == [1, 2, 3, 4, 5, 6, 7]   # every photo placed once
    assert len(placed) == len(set(placed))


def test_deterministic_backend_covers_with_the_standout_and_paces(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(conn, 1)
    struct = _structure(conn)
    # The standout (3) covers the album as the first spread's hero.
    assert struct[0][1] == 3
    # Pacing varies — not every spread is the same size.
    sizes = {len(photos) for _pos, _hero, photos in struct}
    assert len(sizes) > 1


def test_deterministic_backend_is_reproducible(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(conn, 1)
    first = _structure(conn)
    arrange.arrange_album(conn, 1)   # re-run replaces the layout
    assert _structure(conn) == first


def test_proposal_provenance_reports_the_deterministic_proposer(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(conn, 1)
    assert runtime.arrange_backend() == "deterministic"
    prop = proposal.build_proposal(conn, 1)
    assert prop["provider"] == "deterministic"
    assert prop["model"] == "deterministic-v1"
    # The emitted proposal is valid against every eligible asset.
    assert proposal.validate_proposal(prop, proposal.eligible_asset_ids(conn, 1)) == []


def test_source_guard_reroutes_an_incomplete_model_layout(conn, monkeypatch):
    # Model path: a layout that drops photos must NOT be committed as-is — the guard
    # falls back to the chunker so every photo is still placed exactly once.
    monkeypatch.setattr(config, "ARRANGE_BACKEND", None)
    monkeypatch.setattr(
        arrange, "_ask_model",
        lambda photos, **kw: ([{"photos": [1, 2], "hero": 1}], None),  # drops 3-7
    )
    arrange.arrange_album(conn, 1)
    assert sorted(_placed(conn)) == [1, 2, 3, 4, 5, 6, 7]
