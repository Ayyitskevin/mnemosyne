"""Tests for proposal idempotency — a stable proposal per (gallery, request).

These encode the contract's idempotency point: a retry returns the SAME cached
proposal without recomputing; the request fingerprint is sensitive to the inputs that
define the layout; re-arranging invalidates the cache so a new layout yields a new
proposal; and deleting the album drops the cache (it is derived state, never a second
store of authority).
"""
from __future__ import annotations

import sqlite3

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import arrange, auth, config, db, pipeline, proposal, vision
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    c.execute(
        "INSERT INTO albums (id, name, source_dir, gallery_theme) "
        "VALUES (1, 'g', '/x', 'wedding')"
    )
    rows = [
        (1, "getting ready detail", 0.30, 0.80),
        (2, "wide ceremony establishing shot", 0.55, 0.70),
        (3, "couple portrait", 0.92, 0.95),
        (4, "family group", 0.78, 0.82),
        (5, "reception candids", 0.40, 0.60),
    ]
    for pid, scene, hero, keeper in rows:
        c.execute(
            "INSERT INTO photos (id, album_id, storage_key, width, height, scene, "
            "hero_score, keeper_score) VALUES (?, 1, ?, 1200, 800, ?, ?, ?)",
            (pid, f"a1/{pid}.jpg", scene, hero, keeper),
        )
    c.commit()
    return c


def _cache_rows(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM proposal_cache WHERE album_id = 1"
    ).fetchone()["n"]


# --- request_key --------------------------------------------------------------


def test_request_key_is_deterministic(conn):
    assert proposal.request_key(conn, 1) == proposal.request_key(conn, 1)


def test_request_key_changes_with_a_signal(conn):
    before = proposal.request_key(conn, 1)
    conn.execute("UPDATE photos SET hero_score = 0.99 WHERE id = 3")
    conn.commit()
    assert proposal.request_key(conn, 1) != before


def test_request_key_changes_with_theme_and_backend(conn, monkeypatch):
    before = proposal.request_key(conn, 1)
    conn.execute("UPDATE albums SET gallery_theme = 'food' WHERE id = 1")
    conn.commit()
    assert proposal.request_key(conn, 1) != before
    after_theme = proposal.request_key(conn, 1)
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    assert proposal.request_key(conn, 1) != after_theme


# --- cached_proposal ----------------------------------------------------------


def test_retry_returns_cached_without_rebuilding(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(conn, 1)

    calls = {"n": 0}
    real = proposal.build_proposal

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(proposal, "build_proposal", spy)
    first = proposal.cached_proposal(conn, 1)
    second = proposal.cached_proposal(conn, 1)
    assert first == second
    assert calls["n"] == 1            # built once, served from cache on retry
    assert _cache_rows(conn) == 1


def test_arrange_invalidates_the_cache(conn, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(conn, 1)
    proposal.cached_proposal(conn, 1)
    assert _cache_rows(conn) == 1
    arrange.arrange_album(conn, 1)    # re-arrange must drop the derived cache
    assert _cache_rows(conn) == 0


def test_regenerate_yields_a_new_proposal(conn, monkeypatch):
    # Model path with a layout that flips between runs: regenerate invalidates the
    # cache, so the next proposal reflects the new layout rather than the stale one.
    calls = {"n": 0}

    def fake_ask(photos, **kw):
        calls["n"] += 1
        ids = [p["id"] for p in photos]
        order = ids if calls["n"] == 1 else list(reversed(ids))
        return [{"photos": order, "hero": order[0]}], None

    monkeypatch.setattr(config, "ARRANGE_BACKEND", None)
    monkeypatch.setattr(arrange, "_ask_model", fake_ask)

    arrange.arrange_album(conn, 1)
    first = proposal.cached_proposal(conn, 1)
    arrange.arrange_album(conn, 1)            # regenerate -> different layout
    second = proposal.cached_proposal(conn, 1)
    assert first != second
    assert proposal.validate_proposal(second, proposal.eligible_asset_ids(conn, 1)) == []


def test_delete_clears_the_cache(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    arrange.arrange_album(conn, 1)
    proposal.cached_proposal(conn, 1)
    conn.execute("UPDATE albums SET owner_id = NULL, status = 'ready' WHERE id = 1")
    conn.commit()
    assert _cache_rows(conn) == 1
    assert pipeline.delete_album(conn, 1) is True
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM proposal_cache WHERE album_id = 1"
    ).fetchone()["n"] == 0


# --- the endpoint is idempotent ----------------------------------------------


def test_proposal_endpoint_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "dish", "hero_score": 0.5}
    )

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    try:
        client = TestClient(app)
        client.post("/signup", data={"email": "o@example.com", "password": "pw12345"})

        gal = tmp_path / "gal"
        gal.mkdir()
        for i in range(3):
            Image.new("RGB", (10, 8), (i * 40, 0, 0)).save(gal / f"{i}.jpg", "JPEG")

        c = db.connect(db_path)
        uid = c.execute("SELECT id FROM users WHERE email = 'o@example.com'").fetchone()["id"]
        aid = pipeline.enqueue_album(c, name="A", source_dir=gal, owner_id=uid)
        pipeline.process_album(c, aid)
        c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
        c.commit()
        c.close()

        first = client.get(f"/albums/{aid}/proposal.json").json()
        second = client.get(f"/albums/{aid}/proposal.json").json()
        assert first == second
        assert first["placements"]
    finally:
        app.dependency_overrides.clear()
