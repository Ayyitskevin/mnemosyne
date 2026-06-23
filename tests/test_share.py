"""Tests for album share links — the Workstream-5 'hand a finished album to a
client with no account' surface.

These pin *why* the link is shaped the way it is: the token is the only
authorization (so the private album_id never appears publicly), the link is
owner-scoped to mint/revoke (a guessed album_id can't be shared by a stranger),
it self-closes at expiry AND can be revoked early, it only opens a 'ready' album,
and a valid token can't be paired with another tenant's photo_id. The public view
must carry NO edit/delete controls — it is a read-only window.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import arrange, auth, config, db, pipeline, share, vision
from mnemosyne.main import app, get_conn


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(vision, "analyze_one", lambda path: {"scene": "food", "hero_score": 0.5})
    monkeypatch.setattr(arrange, "_ask_model", lambda photos: (None, None))


def _user(conn, email="a@example.com") -> int:
    return auth.create_user(conn, email, "pw12345")["id"]


def _make_gallery(root: Path, n: int = 3) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 10 % 255, 0, 0)).save(root / f"img_{i:02d}.jpg", "JPEG")
    return root


def _ready_album(conn, tmp_path, owner_id: int) -> int:
    """Run a small gallery through the offline pipeline and mark it ready, so there
    are real spreads/photos/bytes for the share view to render."""
    gdir = _make_gallery(tmp_path / "gal")
    aid = pipeline.enqueue_album(conn, name="g", source_dir=gdir, owner_id=owner_id)
    pipeline.process_album(conn, aid)
    conn.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    conn.commit()
    return aid


# --- share module unit behaviour ---------------------------------------------


def test_create_then_resolve_round_trips(conn, tmp_path, no_models):
    uid = _user(conn)
    aid = _ready_album(conn, tmp_path, uid)
    link = share.create_link(conn, aid, uid, ttl_days=30)
    assert link["token"] and link["expires_at"]
    assert share.resolve_token(conn, link["token"]) == aid


def test_revoke_kills_the_link(conn, tmp_path, no_models):
    uid = _user(conn)
    aid = _ready_album(conn, tmp_path, uid)
    token = share.create_link(conn, aid, uid)["token"]
    assert share.revoke_link(conn, aid, uid) is True
    assert share.resolve_token(conn, token) is None
    # Nothing left to revoke the second time.
    assert share.revoke_link(conn, aid, uid) is False


def test_expired_link_does_not_resolve(conn, tmp_path, no_models):
    uid = _user(conn)
    aid = _ready_album(conn, tmp_path, uid)
    token = share.create_link(conn, aid, uid)["token"]
    # Force the expiry into the past — a leaked link self-closes.
    conn.execute(
        "UPDATE albums SET share_expires_at = datetime('now', '-1 day') WHERE id = ?",
        (aid,),
    )
    conn.commit()
    assert share.resolve_token(conn, token) is None


def test_unready_album_link_stays_dark(conn, tmp_path, no_models):
    # A link minted while the album is still building must not open until ready —
    # there are no spreads to show yet.
    uid = _user(conn)
    aid = _ready_album(conn, tmp_path, uid)
    token = share.create_link(conn, aid, uid)["token"]
    conn.execute("UPDATE albums SET status = 'processing' WHERE id = ?", (aid,))
    conn.commit()
    assert share.resolve_token(conn, token) is None


def test_cannot_share_someone_elses_album(conn, tmp_path, no_models):
    owner = _user(conn, "owner@example.com")
    other = _user(conn, "other@example.com")
    aid = _ready_album(conn, tmp_path, owner)
    # Stranger pairs their session with the owner's album_id — no row matches.
    assert share.create_link(conn, aid, other) is None
    assert conn.execute(
        "SELECT share_token FROM albums WHERE id = ?", (aid,)
    ).fetchone()["share_token"] is None


def test_remint_rotates_and_invalidates_old_token(conn, tmp_path, no_models):
    uid = _user(conn)
    aid = _ready_album(conn, tmp_path, uid)
    old = share.create_link(conn, aid, uid)["token"]
    new = share.create_link(conn, aid, uid)["token"]
    assert new != old
    assert share.resolve_token(conn, old) is None
    assert share.resolve_token(conn, new) == aid


def test_shared_photo_key_is_scoped_to_the_token_album(conn, tmp_path, no_models):
    owner = _user(conn, "owner@example.com")
    aid = _ready_album(conn, tmp_path, owner)
    token = share.create_link(conn, aid, owner)["token"]
    mine = conn.execute(
        "SELECT id FROM photos WHERE album_id = ? LIMIT 1", (aid,)
    ).fetchone()["id"]
    assert share.shared_photo_key(conn, token, mine) is not None

    # A second album's photo must NOT be reachable through this token.
    other_album = _ready_album(conn, tmp_path / "two", owner)
    foreign = conn.execute(
        "SELECT id FROM photos WHERE album_id = ? LIMIT 1", (other_album,)
    ).fetchone()["id"]
    assert share.shared_photo_key(conn, token, foreign) is None


# --- route-level: the public surface -----------------------------------------


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


def _signup(client, email="owner@example.com") -> int:
    client.post("/signup", data={"email": email, "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    c.close()
    return uid


def _build_ready(client, owner_id: int, root: Path) -> int:
    c = db.connect(client._db_path)
    gdir = _make_gallery(root / "rgal")
    aid = pipeline.enqueue_album(c, name="g", source_dir=gdir, owner_id=owner_id)
    pipeline.process_album(c, aid)
    c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    c.commit()
    c.close()
    return aid


def test_owner_creates_link_and_public_can_view(web, tmp_path):
    uid = _signup(web)
    aid = _build_ready(web, uid, tmp_path)

    r = web.post(f"/albums/{aid}/share")  # follows redirect back to album page
    assert r.status_code == 200
    c = db.connect(web._db_path)
    token = c.execute("SELECT share_token FROM albums WHERE id = ?", (aid,)).fetchone()["share_token"]
    c.close()
    assert token

    # A brand-new client with no session can open the link.
    anon = TestClient(app)
    v = anon.get(f"/share/{token}")
    assert v.status_code == 200
    # Read-only window: none of the owner's mutation controls leak in.
    assert "/move/" not in v.text and "/delete" not in v.text and "make hero" not in v.text
    # Photos load through the token-scoped image route, not /photo/<id>.
    assert f"/share/{token}/photo/" in v.text
    assert "/photo/" not in v.text.replace(f"/share/{token}/photo/", "")

    # The PDF is downloadable by the link holder.
    p = anon.get(f"/share/{token}/pdf")
    assert p.status_code == 200
    assert p.headers["content-type"] == "application/pdf"

    # And an actual image byte stream resolves.
    img_id = _first_photo_id(web, aid)
    assert anon.get(f"/share/{token}/photo/{img_id}").status_code == 200


def test_revoke_route_dark_then_404(web, tmp_path):
    uid = _signup(web)
    aid = _build_ready(web, uid, tmp_path)
    web.post(f"/albums/{aid}/share")
    c = db.connect(web._db_path)
    token = c.execute("SELECT share_token FROM albums WHERE id = ?", (aid,)).fetchone()["share_token"]
    c.close()

    web.post(f"/albums/{aid}/share/revoke")
    anon = TestClient(app)
    assert anon.get(f"/share/{token}").status_code == 404
    assert anon.get(f"/share/{token}/pdf").status_code == 404


def test_unknown_token_is_404(web):
    anon = TestClient(app)
    assert anon.get("/share/not-a-real-token").status_code == 404


def test_stranger_cannot_share_or_revoke_anothers_album(web, tmp_path):
    owner = _signup(web, "owner@example.com")
    aid = _build_ready(web, owner, tmp_path)

    # A different logged-in user probing the owner's album id gets a 404 (the same
    # gate every album write route funnels through), and no link is minted.
    web.post("/logout")
    _signup(web, "stranger@example.com")
    assert web.post(f"/albums/{aid}/share").status_code == 404
    assert web.post(f"/albums/{aid}/share/revoke").status_code == 404
    c = db.connect(web._db_path)
    assert c.execute(
        "SELECT share_token FROM albums WHERE id = ?", (aid,)
    ).fetchone()["share_token"] is None
    c.close()


def _first_photo_id(client, album_id: int) -> int:
    c = db.connect(client._db_path)
    pid = c.execute(
        "SELECT id FROM photos WHERE album_id = ? ORDER BY id LIMIT 1", (album_id,)
    ).fetchone()["id"]
    c.close()
    return pid
