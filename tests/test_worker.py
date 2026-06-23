"""Tests for the background album worker and the async pipeline body.

These encode *why* the async path must behave: an upload becomes a 'pending'
album, the worker drains it to 'ready', a pipeline error becomes a recorded
'failed' (never a dead worker thread or a stuck album), a crash mid-build is
recoverable, and a retry never duplicates photos. The vision and arrange models
are stubbed — these test the orchestration, not the model output (Rule 5).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from mnemosyne import arrange, auth, db, pipeline, vision, worker


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def no_models(monkeypatch):
    """Replace the two model calls so the pipeline runs offline: vision returns a
    fixed scene+score, and arrange's reasoning call returns nothing so the
    deterministic fallback lays out the spreads."""
    monkeypatch.setattr(
        vision, "analyze_one", lambda path: {"scene": "food", "hero_score": 0.5}
    )
    monkeypatch.setattr(arrange, "_ask_model", lambda photos: None)


def _user(conn) -> int:
    return auth.create_user(conn, "a@example.com", "pw12345")["id"]


def _make_gallery(root, n: int) -> Path:
    g = Path(root) / "gal"
    g.mkdir()
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 10 % 255, 0, 0)).save(g / f"img_{i:02d}.jpg", "JPEG")
    return g


def test_claim_then_run_takes_album_to_ready(conn, tmp_path, no_models):
    uid = _user(conn)
    gdir = _make_gallery(tmp_path, 3)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=gdir, owner_id=uid)

    assert worker._claim_one(conn) == aid
    # Claimed albums are marked processing before the work runs.
    assert conn.execute(
        "SELECT status FROM albums WHERE id = ?", (aid,)
    ).fetchone()["status"] == "processing"

    worker._run_one(conn, aid)
    row = conn.execute("SELECT status, error FROM albums WHERE id = ?", (aid,)).fetchone()
    assert row["status"] == "ready"
    assert row["error"] is None

    photos = conn.execute(
        "SELECT scene, hero_score FROM photos WHERE album_id = ?", (aid,)
    ).fetchall()
    assert len(photos) == 3
    assert all(p["scene"] == "food" for p in photos)
    spreads = conn.execute(
        "SELECT COUNT(*) AS n FROM spreads WHERE album_id = ?", (aid,)
    ).fetchone()["n"]
    assert spreads >= 1


def test_pipeline_error_becomes_failed_not_a_crash(conn, tmp_path, monkeypatch):
    uid = _user(conn)
    gdir = _make_gallery(tmp_path, 2)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=gdir, owner_id=uid)

    def boom(path):
        raise RuntimeError("vision down")

    monkeypatch.setattr(vision, "analyze_one", boom)
    worker._claim_one(conn)
    worker._run_one(conn, aid)  # must not raise

    row = conn.execute("SELECT status, error FROM albums WHERE id = ?", (aid,)).fetchone()
    assert row["status"] == "failed"
    assert "vision down" in row["error"]


def test_recover_stuck_requeues_processing(conn, tmp_path):
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)
    conn.execute("UPDATE albums SET status = 'processing' WHERE id = ?", (aid,))
    conn.commit()

    assert worker.recover_stuck(conn) == 1
    assert conn.execute(
        "SELECT status FROM albums WHERE id = ?", (aid,)
    ).fetchone()["status"] == "pending"


def test_requeue_only_affects_failed_albums(conn, tmp_path):
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)

    conn.execute("UPDATE albums SET status = 'failed', error = 'x' WHERE id = ?", (aid,))
    conn.commit()
    assert pipeline.requeue_album(conn, aid) is True
    row = conn.execute("SELECT status, error FROM albums WHERE id = ?", (aid,)).fetchone()
    assert row["status"] == "pending" and row["error"] is None

    # A ready album is left untouched.
    conn.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    conn.commit()
    assert pipeline.requeue_album(conn, aid) is False
    assert conn.execute(
        "SELECT status FROM albums WHERE id = ?", (aid,)
    ).fetchone()["status"] == "ready"


def test_process_album_is_idempotent_on_retry(conn, tmp_path, no_models):
    uid = _user(conn)
    gdir = _make_gallery(tmp_path, 2)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=gdir, owner_id=uid)

    pipeline.process_album(conn, aid)
    pipeline.process_album(conn, aid)  # a retry must not re-ingest

    n = conn.execute(
        "SELECT COUNT(*) AS c FROM photos WHERE album_id = ?", (aid,)
    ).fetchone()["c"]
    assert n == 2


def test_worker_thread_drains_an_enqueued_album(tmp_path, monkeypatch, no_models):
    """End-to-end: the running thread picks up a pending album and finishes it."""
    db_path = tmp_path / "w.db"
    setup = db.connect(db_path)
    db.migrate(setup)
    uid = auth.create_user(setup, "a@example.com", "pw12345")["id"]
    gdir = _make_gallery(tmp_path, 2)
    aid = pipeline.enqueue_album(setup, name="g", source_dir=gdir, owner_id=uid)
    setup.close()

    w = worker.AlbumWorker(db_path)
    w.start()
    w.notify()
    try:
        status = None
        deadline = time.time() + 5
        while time.time() < deadline:
            c = db.connect(db_path)
            status = c.execute(
                "SELECT status FROM albums WHERE id = ?", (aid,)
            ).fetchone()["status"]
            c.close()
            if status == "ready":
                break
            time.sleep(0.05)
        assert status == "ready"
    finally:
        w.stop()
