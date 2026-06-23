"""Tests for browser album creation — the upload route that lets a signed-up user
make an album without the CLI. These pin *why* each rule exists: the route is the
public boundary where stranger input arrives, so it must cap batch size, keep only
real images, neutralize path-traversal filenames, and always create the album
under the logged-in owner. Since the vision pipeline moved to the background
worker, the route's job is now to save the files and enqueue a 'pending' album and
return — no model runs here (that's covered in test_worker.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import config, db
from mnemosyne.main import app, get_conn


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A logged-in-capable TestClient with uploads pointed at a throwaway dir.
    Lifespan isn't run (no `with`), so no background worker exists — enqueued
    albums simply stay 'pending', which is exactly what these tests assert."""
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    client = TestClient(app)
    client._db_path = db_path
    yield client
    app.dependency_overrides.clear()


def _login(client) -> int:
    client.post("/signup", data={"email": "u@example.com", "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = 'u@example.com'").fetchone()["id"]
    c.close()
    return uid


def _albums(client) -> list:
    c = db.connect(client._db_path)
    rows = c.execute("SELECT * FROM albums ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def test_new_album_form_requires_login(web):
    r = web.get("/albums/new")
    assert "/login" in str(r.url)


def test_upload_enqueues_pending_album_owned_by_user(web):
    uid = _login(web)
    r = web.post(
        "/albums/new",
        data={"name": "Trip"},
        files=[
            ("photos", ("IMG_001.jpg", b"aaa", "image/jpeg")),
            ("photos", ("IMG_002.jpg", b"bbb", "image/jpeg")),
        ],
    )
    assert r.status_code == 200
    # Followed the redirect to the new album, which is still building.
    assert "designing your album" in r.text.lower()

    rows = _albums(web)
    assert len(rows) == 1
    album = rows[0]
    assert album["owner_id"] == uid
    assert album["name"] == "Trip"
    assert album["status"] == "pending"
    # Files actually landed on disk for the worker to ingest later.
    assert sorted(p.name for p in Path(album["source_dir"]).iterdir()) == [
        "IMG_001.jpg",
        "IMG_002.jpg",
    ]


def test_blank_name_defaults_to_untitled(web):
    _login(web)
    web.post(
        "/albums/new",
        data={"name": "   "},
        files=[("photos", ("a.jpg", b"x", "image/jpeg"))],
    )
    assert _albums(web)[0]["name"] == "Untitled album"


def test_oversized_batch_is_rejected_without_enqueuing(web, monkeypatch):
    monkeypatch.setattr(config, "MAX_ALBUM_UPLOAD", 2)
    _login(web)
    r = web.post(
        "/albums/new",
        files=[("photos", (f"{i}.jpg", b"x", "image/jpeg")) for i in range(3)],
    )
    assert r.status_code == 200
    assert "too many photos" in r.text.lower()
    assert _albums(web) == []  # nothing was created


def test_non_image_upload_is_rejected(web):
    _login(web)
    r = web.post(
        "/albums/new",
        files=[("photos", ("notes.txt", b"hello", "text/plain"))],
    )
    assert "no usable images" in r.text.lower()
    assert _albums(web) == []


def test_traversal_filename_is_reduced_to_basename(web):
    _login(web)
    web.post(
        "/albums/new",
        files=[("photos", ("../../etc/evil.jpg", b"x", "image/jpeg"))],
    )
    src = Path(_albums(web)[0]["source_dir"])
    saved = list(src.iterdir())
    # The crafted path can't escape the upload root: stored as its bare basename.
    assert [p.name for p in saved] == ["evil.jpg"]
    assert saved[0].parent == src
