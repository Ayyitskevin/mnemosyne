"""Regenerate layout — re-arrange without re-vision."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import arrange, auth, config, db, pipeline, vision
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def no_models(monkeypatch):
    calls = {"n": 0}

    def fake_ask(photos):
        calls["n"] += 1
        ids = [p["id"] for p in photos]
        if calls["n"] == 1:
            return [{"photos": ids, "hero": ids[0]}], None
        return [{"photos": list(reversed(ids)), "hero": ids[-1]}], None

    monkeypatch.setattr(vision, "analyze_one", lambda path: {"scene": "dish", "hero_score": 0.5})
    monkeypatch.setattr(arrange, "_ask_model", fake_ask)


def _gallery(root: Path, n: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (10, 8), (i * 40, 0, 0)).save(root / f"{i:02d}.jpg", "JPEG")
    return root


def test_regenerate_layout_keeps_vision(conn, tmp_path, no_models):
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = pipeline.enqueue_album(conn, name="G", source_dir=_gallery(tmp_path / "g"), owner_id=uid)
    pipeline.process_album(conn, aid)
    conn.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    conn.commit()

    scenes_before = [
        r["scene"]
        for r in conn.execute(
            "SELECT scene FROM photos WHERE album_id = ? ORDER BY id", (aid,)
        ).fetchall()
    ]
    hero_before = conn.execute(
        "SELECT hero_photo_id FROM spreads WHERE album_id = ? ORDER BY position LIMIT 1",
        (aid,),
    ).fetchone()["hero_photo_id"]

    spreads = pipeline.regenerate_layout(conn, aid)
    assert spreads == 1

    scenes_after = [
        r["scene"]
        for r in conn.execute(
            "SELECT scene FROM photos WHERE album_id = ? ORDER BY id", (aid,)
        ).fetchall()
    ]
    assert scenes_after == scenes_before

    hero_after = conn.execute(
        "SELECT hero_photo_id FROM spreads WHERE album_id = ? ORDER BY position LIMIT 1",
        (aid,),
    ).fetchone()["hero_photo_id"]
    assert hero_after != hero_before


def test_regenerate_layout_rejects_non_ready(conn, tmp_path, no_models):
    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = pipeline.enqueue_album(conn, name="G", source_dir=_gallery(tmp_path / "g2"), owner_id=uid)
    with pytest.raises(LookupError):
        pipeline.regenerate_layout(conn, aid)


@pytest.fixture
def web(tmp_path, monkeypatch, no_models):
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


def _signup(client: TestClient) -> int:
    client.post("/signup", data={"email": "regen@example.com", "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = 'regen@example.com'").fetchone()["id"]
    c.close()
    return uid


def _ready(client: TestClient, uid: int, root: Path) -> int:
    c = db.connect(client._db_path)
    aid = pipeline.enqueue_album(c, name="Regen", source_dir=_gallery(root / "gal"), owner_id=uid)
    pipeline.process_album(c, aid)
    c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    c.commit()
    c.close()
    return aid


def test_regenerate_route(web, tmp_path):
    uid = _signup(web)
    aid = _ready(web, uid, tmp_path)
    r = web.post(f"/albums/{aid}/regenerate", follow_redirects=False)
    assert r.status_code == 303
    assert f"albums/{aid}?regenerated=1" in r.headers["location"]
    page = web.get(f"/albums/{aid}?regenerated=1")
    assert b"Layout regenerated" in page.content


def test_healthz_reports_backends(web):
    r = web.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "vision" in body["backends"]
    assert "arrange" in body["backends"]