"""Plutus storefront cross-sell — offer URL normalization and persistence."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mnemosyne import arrange, auth, config, db, pipeline, plutus_link, share, vision
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


def test_normalize_full_offer_url():
    url = "https://plutus.example.com/store/demo/offer/abc123"
    assert plutus_link.normalize_offer_url(url) == url


def test_normalize_rejects_bad_paths():
    assert plutus_link.normalize_offer_url("https://evil.com/offer/x") is None
    assert plutus_link.normalize_offer_url("not-a-url") is None
    assert plutus_link.normalize_offer_url("") is None


def test_normalize_path_with_plutus_base(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_URL", "https://plutus.example.com")
    assert (
        plutus_link.normalize_offer_url("/store/demo/offer/tok")
        == "https://plutus.example.com/store/demo/offer/tok"
    )


def test_normalize_path_without_base_returns_none(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_URL", None)
    assert plutus_link.normalize_offer_url("/store/demo/offer/tok") is None


def test_save_offer_url_owner_scoped(conn):
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    other = auth.create_user(conn, "b@example.com", "pw12345")["id"]
    aid = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id) VALUES ('a', '/x', ?)",
        (uid,),
    ).lastrowid
    conn.commit()
    url = "https://plutus.example.com/store/demo/offer/tok"
    assert plutus_link.save_offer_url(conn, aid, uid, url) == url
    row = conn.execute(
        "SELECT plutus_offer_url FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    assert row["plutus_offer_url"] == url
    assert plutus_link.save_offer_url(conn, aid, other, url) is None


def _make_gallery(root: Path, n: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 10 % 255, 0, 0)).save(root / f"img_{i:02d}.jpg", "JPEG")
    return root


def _signup(client, email="owner@example.com") -> int:
    client.post("/signup", data={"email": email, "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    c.close()
    return uid


def _build_ready(client, owner_id: int, root: Path) -> int:
    c = db.connect(client._db_path)
    gdir = _make_gallery(root / "gal")
    aid = pipeline.enqueue_album(c, name="g", source_dir=gdir, owner_id=owner_id)
    pipeline.process_album(c, aid)
    c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    c.commit()
    c.close()
    return aid


def test_share_view_shows_order_prints_cta(web, tmp_path):
    uid = _signup(web)
    aid = _build_ready(web, uid, tmp_path)
    offer = "https://plutus.example.com/store/demo/offer/tok"
    c = db.connect(web._db_path)
    plutus_link.save_offer_url(c, aid, uid, offer)
    token = share.create_link(c, aid, uid)["token"]
    c.close()

    anon = TestClient(app)
    r = anon.get(f"/share/{token}")
    assert r.status_code == 200
    assert "Order prints" in r.text
    assert offer in r.text


def test_album_post_plutus_link(web, tmp_path):
    uid = _signup(web)
    aid = _build_ready(web, uid, tmp_path)
    offer = "https://plutus.example.com/store/demo/offer/tok"

    r = web.post(
        f"/albums/{aid}/plutus-link",
        data={"plutus_offer_url": offer},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "plutus_saved=1" in r.headers["location"]

    c = db.connect(web._db_path)
    row = c.execute(
        "SELECT plutus_offer_url FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    c.close()
    assert row["plutus_offer_url"] == offer