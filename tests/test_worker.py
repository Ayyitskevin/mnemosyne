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
        vision, "analyze_one", lambda path, **kw: {"scene": "food", "hero_score": 0.5}
    )
    monkeypatch.setattr(arrange, "_ask_model", lambda photos, **kw: (None, None))


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

    claim = worker._claim_one(conn)
    assert claim is not None
    claimed_id, claim_token = claim
    assert claimed_id == aid
    # Claimed albums are marked processing before the work runs.
    row = conn.execute(
        "SELECT status, claim_token, attempts, started_at, finished_at, "
        "last_heartbeat FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "processing"
    assert row["claim_token"] == claim_token
    assert row["attempts"] == 1
    assert row["started_at"] is not None
    assert row["finished_at"] is None
    assert row["last_heartbeat"] is not None

    worker._run_one(conn, aid, claim_token)
    row = conn.execute(
        "SELECT status, error, finished_at, claimed_at, claim_token, "
        "last_heartbeat FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "ready"
    assert row["error"] is None
    assert row["finished_at"] is not None
    assert row["claimed_at"] is None
    assert row["claim_token"] is None
    assert row["last_heartbeat"] is None

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

    def boom(path, **kw):
        raise RuntimeError("vision down")

    monkeypatch.setattr(vision, "analyze_one", boom)
    claim = worker._claim_one(conn)
    assert claim is not None
    worker._run_one(conn, aid, claim[1])  # must not raise

    row = conn.execute(
        "SELECT status, error, finished_at, claim_token FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "failed"
    assert "vision down" in row["error"]
    assert row["finished_at"] is not None
    assert row["claim_token"] is None


def test_reclaim_requeues_album_with_expired_lease(conn, tmp_path):
    """A 'processing' album whose claim is older than the lease belongs to a dead
    worker — it must come back to 'pending' so another worker finishes it."""
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)
    conn.execute(
        "UPDATE albums SET status = 'processing', "
        "claimed_at = datetime('now', '-3600 seconds') WHERE id = ?",
        (aid,),
    )
    conn.commit()

    assert worker.reclaim_stale(conn, lease_seconds=900) == 1
    row = conn.execute(
        "SELECT status, claim_token, last_heartbeat FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claim_token"] is None
    assert row["last_heartbeat"] is None


def test_reclaim_treats_unstamped_processing_as_stale(conn, tmp_path):
    """A 'processing' row with no claimed_at is from an older/unknown claim and is
    reclaimed — this is what recovers albums stuck before the lease existed."""
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)
    conn.execute("UPDATE albums SET status = 'processing' WHERE id = ?", (aid,))
    conn.commit()

    assert worker.reclaim_stale(conn) == 1
    row = conn.execute(
        "SELECT status, claim_token, last_heartbeat FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claim_token"] is None
    assert row["last_heartbeat"] is None


def test_reclaim_leaves_a_fresh_lease_alone(conn, tmp_path):
    """A live sibling's in-flight album has a fresh claimed_at — reclaim must NOT
    yank it, or two workers would process the same album at once."""
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)
    conn.execute(
        "UPDATE albums SET status = 'processing', claimed_at = datetime('now') "
        "WHERE id = ?",
        (aid,),
    )
    conn.commit()

    assert worker.reclaim_stale(conn, lease_seconds=900) == 0
    assert conn.execute(
        "SELECT status FROM albums WHERE id = ?", (aid,)
    ).fetchone()["status"] == "processing"


def test_atomic_claim_prevents_two_workers_taking_one_album(tmp_path):
    """The whole point of the multi-process turn: two workers on the same DB must
    never both claim the same pending album. The second worker's conditional
    UPDATE matches no row (already 'processing'), so it claims nothing."""
    path = tmp_path / "q.db"
    a = db.connect(path)
    db.migrate(a)
    b = db.connect(path)  # a second process's worker connection
    uid = auth.create_user(a, "a@example.com", "pw12345")["id"]
    aid = pipeline.enqueue_album(a, name="g", source_dir=tmp_path, owner_id=uid)

    # Reproduce the race window explicitly: both read the same pending candidate.
    r1 = a.execute(
        "SELECT id FROM albums WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    r2 = b.execute(
        "SELECT id FROM albums WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    assert r1["id"] == r2["id"] == aid

    claim = (
        "UPDATE albums SET status = 'processing', claimed_at = datetime('now'), "
        "claim_token = ? WHERE id = ? AND status = 'pending'"
    )
    c1 = a.execute(claim, ("worker-a", aid))
    a.commit()
    c2 = b.execute(claim, ("worker-b", aid))
    b.commit()
    assert (c1.rowcount, c2.rowcount) == (1, 0)

    # And through the real function: once a has it, b claims nothing.
    assert worker._claim_one(b) is None
    a.close()
    b.close()


def test_superseded_claim_cannot_overwrite_newer_result(conn, tmp_path):
    """If a job outruns the lease, the old worker may still finish later. Its
    final write must not clobber the newer worker's live claim/result."""
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)
    old_claim = worker._claim_one(conn)
    assert old_claim is not None
    old_token = old_claim[1]

    conn.execute(
        "UPDATE albums SET claimed_at = datetime('now', '-3600 seconds') "
        "WHERE id = ?",
        (aid,),
    )
    conn.commit()
    assert worker.reclaim_stale(conn, lease_seconds=900) == 1
    new_claim = worker._claim_one(conn)
    assert new_claim is not None
    new_token = new_claim[1]

    assert worker._finish_claim(conn, aid, new_token, "failed", "new result")
    assert not worker._finish_claim(conn, aid, old_token, "ready", None)
    row = conn.execute(
        "SELECT status, error, claim_token FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "new result"
    assert row["claim_token"] is None


def test_requeue_only_affects_failed_albums(conn, tmp_path):
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=tmp_path, owner_id=uid)

    conn.execute("UPDATE albums SET status = 'failed', error = 'x' WHERE id = ?", (aid,))
    conn.commit()
    assert pipeline.requeue_album(conn, aid) is True
    row = conn.execute(
        "SELECT status, error, claimed_at, claim_token, started_at, finished_at, "
        "last_heartbeat FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["status"] == "pending" and row["error"] is None
    assert row["claimed_at"] is None and row["claim_token"] is None
    assert row["started_at"] is None and row["finished_at"] is None
    assert row["last_heartbeat"] is None

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
