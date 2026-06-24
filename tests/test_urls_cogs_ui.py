"""Public share URLs and COGS surfaced in the album UI."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import arrange, auth, config, db, pipeline, share, urls, usage, vision
from mnemosyne.main import app, get_conn


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(vision, "analyze_one", lambda path: {"scene": "food", "hero_score": 0.5})
    monkeypatch.setattr(arrange, "_ask_model", lambda photos: (None, None))


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


def _make_gallery(root: Path, n: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 10 % 255, 0, 0)).save(root / f"img_{i:02d}.jpg", "JPEG")
    return root


def _signup(client: TestClient, email: str = "cogs@example.com") -> int:
    client.post("/signup", data={"email": email, "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    c.close()
    return uid


def _build_ready(client: TestClient, owner_id: int, root: Path) -> int:
    c = db.connect(client._db_path)
    gdir = _make_gallery(root / "gal")
    aid = pipeline.enqueue_album(c, name="COGS Album", source_dir=gdir, owner_id=owner_id)
    pipeline.process_album(c, aid)
    c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    c.commit()
    c.close()
    return aid


def test_share_url_uses_public_base(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://albums.kleephotography.com")
    assert urls.share_url(None, "tok123") == "https://albums.kleephotography.com/share/tok123"


def test_album_page_shows_cogs_and_public_share(web, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://mnemosyne.test")
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 6.0)

    uid = _signup(web)
    aid = _build_ready(web, uid, tmp_path)

    c = db.connect(web._db_path)
    usage.record(
        c,
        album_id=aid,
        photo_id=1,
        stage="vision",
        backend="grok",
        model="grok-test",
        tokens={"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        latency=0.5,
    )
    usage.record(
        c,
        album_id=aid,
        photo_id=None,
        stage="arrange",
        backend="grok",
        model="grok-test",
        tokens={"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
        latency=0.2,
    )
    share.create_link(c, aid, uid, ttl_days=7)
    c.close()

    page = web.get(f"/albums/{aid}")
    assert page.status_code == 200
    assert b"Cloud inference COGS" in page.content
    assert b"2 billed call" in page.content
    assert b"vision" in page.content
    assert b"arrange" in page.content
    assert b"https://mnemosyne.test/share/" in page.content

    index = web.get("/albums")
    assert b"cloud COGS" in index.content
    assert b"ready" in index.content