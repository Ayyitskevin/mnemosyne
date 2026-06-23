"""Tests for browser album creation — the upload route that lets a signed-up user
make an album without the CLI. These pin *why* each rule exists: the route is the
public boundary where stranger input arrives, so it must cap batch size (the
pipeline runs inline and an unbounded gallery would hang the request), keep only
real images, neutralize path-traversal filenames, and always build under the
logged-in owner so an upload can't be misattributed. The vision pipeline itself is
stubbed — these test the route's wiring, not the model.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import config, db, pipeline
from mnemosyne.main import app, get_conn


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A logged-in-capable TestClient with uploads pointed at a throwaway dir and
    build_album stubbed, so the route can be exercised without ollama. The stub
    records what it was handed and inserts a real album row so the post-redirect
    render works."""
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")

    captured: dict = {}

    def fake_build_album(conn, *, name, source_dir, owner_id):
        files = sorted(p.name for p in Path(source_dir).iterdir())
        captured.update(name=name, owner_id=owner_id, files=files, source_dir=source_dir)
        cur = conn.execute(
            "INSERT INTO albums (name, source_dir, owner_id) VALUES (?, ?, ?)",
            (name, str(source_dir), owner_id),
        )
        conn.commit()
        return {"album_id": cur.lastrowid, "looked": len(files), "spreads": 0}

    monkeypatch.setattr(pipeline, "build_album", fake_build_album)

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    client = TestClient(app)
    client._captured = captured  # stash for assertions
    client._db_path = db_path
    yield client
    app.dependency_overrides.clear()


def _login(client) -> int:
    """Sign up (which logs in) and return the new user's id."""
    client.post("/signup", data={"email": "u@example.com", "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = 'u@example.com'").fetchone()["id"]
    c.close()
    return uid


def test_new_album_form_requires_login(web):
    r = web.get("/albums/new")
    assert "/login" in str(r.url)


def test_upload_builds_album_owned_by_user(web):
    uid = _login(web)
    r = web.post(
        "/albums/new",
        data={"name": "Trip"},
        files=[
            ("photos", ("IMG_001.jpg", b"aaa", "image/jpeg")),
            ("photos", ("IMG_002.jpg", b"bbb", "image/jpeg")),
        ],
    )
    assert r.status_code == 200  # followed the redirect to /albums/<id>
    cap = web._captured
    assert cap["owner_id"] == uid
    assert cap["name"] == "Trip"
    assert cap["files"] == ["IMG_001.jpg", "IMG_002.jpg"]
    # Files actually landed on disk for /photo/<id> to serve later.
    assert sorted(p.name for p in Path(cap["source_dir"]).iterdir()) == [
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
    assert web._captured["name"] == "Untitled album"


def test_oversized_batch_is_rejected_without_building(web, monkeypatch):
    monkeypatch.setattr(config, "MAX_ALBUM_UPLOAD", 2)
    _login(web)
    r = web.post(
        "/albums/new",
        files=[
            ("photos", (f"{i}.jpg", b"x", "image/jpeg")) for i in range(3)
        ],
    )
    assert r.status_code == 200
    assert "too many photos" in r.text.lower()
    assert web._captured == {}  # build_album never ran


def test_non_image_upload_is_rejected(web):
    _login(web)
    r = web.post(
        "/albums/new",
        files=[("photos", ("notes.txt", b"hello", "text/plain"))],
    )
    assert "no usable images" in r.text.lower()
    assert web._captured == {}


def test_traversal_filename_is_reduced_to_basename(web):
    _login(web)
    web.post(
        "/albums/new",
        files=[("photos", ("../../etc/evil.jpg", b"x", "image/jpeg"))],
    )
    cap = web._captured
    # The crafted path can't escape the upload root: stored as its bare basename.
    assert cap["files"] == ["evil.jpg"]
    saved = list(Path(cap["source_dir"]).iterdir())
    assert len(saved) == 1
    assert saved[0].parent == Path(cap["source_dir"])
