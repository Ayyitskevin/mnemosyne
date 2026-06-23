"""Tests for album deletion — a destructive, owner-only operation.

These pin *why* delete is careful: the schema has no ON DELETE CASCADE and
foreign keys are enforced, so children must be removed by hand in FK-safe order
(placements -> spreads -> photos -> album); the uploaded folder is removed ONLY
when it lives under UPLOAD_DIR (never a CLI album's original gallery on disk);
and the routes must 404 a cross-tenant id rather than confirm it exists.
"""
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
    """Run the pipeline offline so delete has real photos/spreads to cascade."""
    monkeypatch.setattr(
        vision, "analyze_one", lambda path: {"scene": "food", "hero_score": 0.5}
    )
    monkeypatch.setattr(arrange, "_ask_model", lambda photos: None)


def _user(conn, email="a@example.com") -> int:
    return auth.create_user(conn, email, "pw12345")["id"]


def _make_gallery(root: Path, n: int = 3) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (8, 8), (i * 10 % 255, 0, 0)).save(
            root / f"img_{i:02d}.jpg", "JPEG"
        )
    return root


def _counts(conn, album_id: int) -> dict:
    def n(table, col):
        return conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE {col} = ?", (album_id,)
        ).fetchone()["c"]

    return {
        "albums": n("albums", "id"),
        "photos": n("photos", "album_id"),
        "spreads": n("spreads", "album_id"),
        "placements": conn.execute(
            "SELECT COUNT(*) AS c FROM placements WHERE spread_id IN "
            "(SELECT id FROM spreads WHERE album_id = ?)",
            (album_id,),
        ).fetchone()["c"],
    }


def test_delete_cascades_children_and_removes_upload_dir(conn, tmp_path, monkeypatch, no_models):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    udir = _make_gallery(config.UPLOAD_DIR / "u1_abc")
    uid = _user(conn)
    aid = pipeline.enqueue_album(conn, name="g", source_dir=udir, owner_id=uid)
    pipeline.process_album(conn, aid)

    before = _counts(conn, aid)
    assert before["photos"] == 3 and before["spreads"] >= 1 and before["placements"] >= 1

    assert pipeline.delete_album(conn, aid) is True
    after = _counts(conn, aid)
    assert after == {"albums": 0, "photos": 0, "spreads": 0, "placements": 0}
    # The web upload folder (under UPLOAD_DIR) is cleaned off disk.
    assert not udir.exists()


def test_delete_leaves_cli_gallery_on_disk(conn, tmp_path, monkeypatch, no_models):
    # A CLI album's source_dir is the operator's own gallery, NOT under UPLOAD_DIR.
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    gallery = _make_gallery(tmp_path / "my_photos")
    uid = _user(conn)
    summary = pipeline.build_album(conn, name="g", source_dir=gallery, owner_id=uid)
    aid = summary["album_id"]

    assert pipeline.delete_album(conn, aid) is True
    assert _counts(conn, aid)["albums"] == 0
    # The original gallery is untouched — delete only removes folders it owns.
    assert gallery.exists()
    assert sorted(p.name for p in gallery.iterdir()) == [
        "img_00.jpg",
        "img_01.jpg",
        "img_02.jpg",
    ]


def test_delete_missing_album_is_false(conn):
    assert pipeline.delete_album(conn, 999) is False


# --- route-level owner gate ---------------------------------------------------


@pytest.fixture
def web(tmp_path, monkeypatch):
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


def _signup(client, email) -> int:
    client.post("/signup", data={"email": email, "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    c.close()
    return uid


def _album_count(client) -> int:
    c = db.connect(client._db_path)
    n = c.execute("SELECT COUNT(*) AS c FROM albums").fetchone()["c"]
    c.close()
    return n


def test_owner_can_delete_via_route(web):
    uid = _signup(web, "owner@example.com")
    c = db.connect(web._db_path)
    aid = pipeline.enqueue_album(c, name="g", source_dir=web._db_path.parent, owner_id=uid)
    c.close()

    r = web.post(f"/albums/{aid}/delete")
    assert r.status_code == 200  # followed redirect to /albums
    assert str(r.url).endswith("/albums")
    assert _album_count(web) == 0


def test_cross_tenant_delete_is_404_and_keeps_album(web):
    # Album belongs to B; A is logged in and probes it.
    c = db.connect(web._db_path)
    bid = c.execute(
        "INSERT INTO users (email, password_hash) VALUES ('b@example.com', 'x')"
    ).lastrowid
    aid = pipeline.enqueue_album(c, name="b-album", source_dir=web._db_path.parent, owner_id=bid)
    c.commit()
    c.close()

    _signup(web, "a@example.com")
    assert web.get(f"/albums/{aid}/delete").status_code == 404
    assert web.post(f"/albums/{aid}/delete").status_code == 404
    assert _album_count(web) == 1  # B's album survived A's probe
