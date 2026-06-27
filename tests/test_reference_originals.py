"""Tests for reference mode — no media duplication for Mise imports.

These encode the retire-readiness invariants: with the opt-in flag, a Mise import
references the gallery's originals in place (storage_key = the original's path) and
copies nothing into mnemosyne's store; deleting such an album never removes the
referenced originals; and the default (copy) behavior is unchanged for uploads and
when the flag is off. /healthz reports the posture.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import (
    arrange,
    auth,
    config,
    db,
    ingest,
    pipeline,
    runtime,
    vision,
)
from mnemosyne.main import app


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def _seed(dir_: Path, n: int = 3) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (12, 8), (i * 30, 0, 0)).save(dir_ / f"{i:02d}.jpg", "JPEG")
    return dir_


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "x", "hero_score": 0.5}
    )
    monkeypatch.setattr(arrange, "_ask_model", lambda photos, **kw: (None, None))


# --- ingest_photos directly --------------------------------------------------


def test_reference_ingest_records_abspath_and_copies_nothing(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    src = _seed(tmp_path / "mise" / "7" / "original")
    conn.execute("INSERT INTO albums (id, name, source_dir) VALUES (1, 'g', '/x')")
    conn.commit()

    ingest.ingest_photos(conn, 1, src, reference=True)

    keys = [r["storage_key"] for r in conn.execute(
        "SELECT storage_key FROM photos WHERE album_id = 1 ORDER BY id"
    )]
    # Each key is the original's absolute path, and the original still exists there.
    for key in keys:
        assert Path(key).is_absolute() and Path(key).is_file()
        assert str(src.resolve()) in key
    # Nothing was copied into mnemosyne's store.
    assert not (config.UPLOAD_DIR / "a1").exists()


def test_copy_ingest_is_unchanged(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    src = _seed(tmp_path / "gal")
    conn.execute("INSERT INTO albums (id, name, source_dir) VALUES (1, 'g', '/x')")
    conn.commit()

    ingest.ingest_photos(conn, 1, src)  # default copy mode

    keys = [r["storage_key"] for r in conn.execute("SELECT storage_key FROM photos")]
    assert all(k.startswith("a1/") for k in keys)
    assert (config.UPLOAD_DIR / "a1").is_dir()        # bytes copied into the store


# --- process_album decision --------------------------------------------------


def _mise_album(conn, owner_id: int, src: Path, gid: int | None) -> int:
    return ingest.create_album(
        conn, name="G", source_dir=src, owner_id=owner_id, status="pending",
        mise_gallery_id=gid,
    )


def test_process_album_references_for_mise_when_enabled(conn, tmp_path, monkeypatch, no_models):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "REFERENCE_MISE_ORIGINALS", True)
    src = _seed(tmp_path / "mise" / "9" / "original")
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = _mise_album(conn, uid, src, gid=9)

    pipeline.process_album(conn, aid)

    keys = [r["storage_key"] for r in conn.execute(
        "SELECT storage_key FROM photos WHERE album_id = ?", (aid,)
    )]
    assert keys and all(Path(k).is_absolute() for k in keys)
    assert not (config.UPLOAD_DIR / f"a{aid}").exists()   # no second copy
    assert sorted(src.iterdir())                          # originals untouched


def test_process_album_copies_by_default(conn, tmp_path, monkeypatch, no_models):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    # flag off (default) → copy even for a Mise album
    src = _seed(tmp_path / "mise" / "9" / "original")
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = _mise_album(conn, uid, src, gid=9)

    pipeline.process_album(conn, aid)
    keys = [r["storage_key"] for r in conn.execute(
        "SELECT storage_key FROM photos WHERE album_id = ?", (aid,)
    )]
    assert all(k.startswith(f"a{aid}/") for k in keys)
    assert (config.UPLOAD_DIR / f"a{aid}").is_dir()


def test_upload_album_always_copies_even_with_flag_on(conn, tmp_path, monkeypatch, no_models):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "REFERENCE_MISE_ORIGINALS", True)
    src = _seed(tmp_path / "gal")
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = _mise_album(conn, uid, src, gid=None)   # no mise_gallery_id → an upload

    pipeline.process_album(conn, aid)
    keys = [r["storage_key"] for r in conn.execute(
        "SELECT storage_key FROM photos WHERE album_id = ?", (aid,)
    )]
    assert all(k.startswith(f"a{aid}/") for k in keys)


# --- delete never removes referenced originals -------------------------------


def test_delete_keeps_referenced_originals(conn, tmp_path, monkeypatch, no_models):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "REFERENCE_MISE_ORIGINALS", True)
    src = _seed(tmp_path / "mise" / "9" / "original")
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = _mise_album(conn, uid, src, gid=9)
    pipeline.process_album(conn, aid)
    conn.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    conn.commit()

    originals = sorted(src.iterdir())
    assert pipeline.delete_album(conn, aid) is True
    # The album rows are gone, but Mise's originals are untouched.
    assert conn.execute("SELECT COUNT(*) AS n FROM photos WHERE album_id = ?", (aid,)).fetchone()["n"] == 0
    assert sorted(src.iterdir()) == originals


# --- posture is observable ---------------------------------------------------


def test_runtime_reports_reference_posture(monkeypatch):
    monkeypatch.setattr(config, "REFERENCE_MISE_ORIGINALS", True)
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    assert runtime.reference_originals() is True
    assert runtime.backend_status()["reference_mise_originals"] is True
    # Reference mode can't apply to a remote bucket — off regardless of the flag.
    monkeypatch.setattr(config, "STORAGE_BACKEND", "r2")
    assert runtime.reference_originals() is False


def test_healthz_exposes_reference_posture():
    body = TestClient(app).get("/healthz").json()
    assert "reference_mise_originals" in body["backends"]
