"""Mise gallery import — client, path resolution, enqueue."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mnemosyne import arrange, auth, config, db, mise_client, mise_import, pipeline, vision
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "food", "hero_score": 0.5}
    )
    monkeypatch.setattr(arrange, "_ask_model", lambda photos, **kw: (None, None))


@pytest.fixture
def web(tmp_path, monkeypatch, no_models):
    db_path = tmp_path / "web.db"
    db.migrate(db.connect(db_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "tok")

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


def _gallery(gid: int, root: Path, *, run_id: int | None = 42) -> dict:
    return {
        "id": gid,
        "title": f"Gallery {gid}",
        "originals_path": str(root / str(gid) / "original"),
        "plutus_last_run_id": run_id,
    }


def _seed_originals(root: Path, gid: int, n: int = 2) -> Path:
    dest = root / str(gid) / "original"
    dest.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 20, 0, 0)).save(dest / f"{i:02d}.jpg", "JPEG")
    return dest


def test_mise_client_list_galleries(monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"galleries": [{"id": 1, "title": "A"}]}

    class _Inner:
        def get(self, url, params=None, headers=None):
            assert "Bearer tok" in headers["Authorization"]
            return _Resp()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return _Inner()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "tok")
    monkeypatch.setattr(mise_client.httpx, "Client", _Client)
    body = mise_client.list_galleries(published=True)
    assert body["galleries"][0]["id"] == 1


def test_import_gallery_enqueues_with_metadata(conn, tmp_path, monkeypatch):
    gid = 7
    media = tmp_path / "media"
    _seed_originals(media, gid)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", media)
    monkeypatch.setattr(
        mise_client,
        "get_gallery",
        lambda gallery_id: _gallery(gid, media) if gallery_id == gid else None,
    )
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "tok")

    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    aid = mise_import.import_gallery(conn, owner_id=uid, gallery_id=gid, gallery_theme="wedding")
    row = conn.execute(
        "SELECT mise_gallery_id, plutus_run_id, gallery_theme, status FROM albums WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["mise_gallery_id"] == gid
    assert row["plutus_run_id"] == 42
    assert row["gallery_theme"] == "wedding"
    assert row["status"] == "pending"


def test_import_rejects_duplicate(conn, tmp_path, monkeypatch):
    gid = 3
    media = tmp_path / "media"
    _seed_originals(media, gid)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", media)
    monkeypatch.setattr(mise_client, "get_gallery", lambda gallery_id: _gallery(gid, media))
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "tok")

    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    mise_import.import_gallery(conn, owner_id=uid, gallery_id=gid)
    with pytest.raises(mise_import.MiseImportError, match="already imported"):
        mise_import.import_gallery(conn, owner_id=uid, gallery_id=gid)


def test_import_mise_route(web, tmp_path, monkeypatch):
    gid = 5
    media = tmp_path / "media"
    _seed_originals(media, gid)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", media)
    monkeypatch.setattr(
        mise_client,
        "list_galleries",
        lambda **kw: {"galleries": [_gallery(gid, media)]},
    )
    monkeypatch.setattr(mise_client, "get_gallery", lambda gallery_id: _gallery(gid, media))

    web.post("/signup", data={"email": "m@example.com", "password": "pw12345"})
    page = web.get("/albums/import/mise")
    assert page.status_code == 200
    assert "Gallery 5" in page.text

    r = web.post(
        "/albums/import/mise",
        data={"gallery_id": str(gid), "gallery_theme": "event"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/albums/" in r.headers["location"]