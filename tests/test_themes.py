"""Gallery themes — prompt selection and normalization."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from mnemosyne import arrange, auth, db, ingest, pipeline, themes, vision


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def test_normalize_theme_defaults_invalid_to_food():
    assert themes.normalize_theme(None) == "food"
    assert themes.normalize_theme("") == "food"
    assert themes.normalize_theme("  WEDDING  ") == "wedding"
    assert themes.normalize_theme("portraits") == "food"


def test_vision_prompt_varies_by_theme():
    food = themes.vision_prompt("food")
    wedding = themes.vision_prompt("wedding")
    assert "restaurant" in food.lower() or "food" in food.lower()
    assert "wedding" in wedding.lower()
    assert food != wedding


def test_arrange_system_varies_by_theme():
    food = themes.arrange_system("food")
    event = themes.arrange_system("event")
    assert "restaurant" in food.lower() or "food" in food.lower()
    assert "event" in event.lower()
    assert food != event


def test_create_album_persists_theme(conn):
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    aid = ingest.create_album(
        conn, name="t", source_dir="/tmp/g", owner_id=uid, gallery_theme="wedding"
    )
    row = conn.execute(
        "SELECT gallery_theme FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    assert row["gallery_theme"] == "wedding"


def test_look_at_album_passes_theme_to_analyze(conn, tmp_path, monkeypatch):
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    gdir = tmp_path / "gal"
    gdir.mkdir()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(gdir / "a.jpg", "JPEG")
    aid = ingest.ingest_folder(
        conn, name="g", source_dir=gdir, owner_id=uid, gallery_theme="event"
    )
    seen: dict[str, str] = {}

    def fake_analyze(path, **kw):
        seen["theme"] = kw.get("theme", "")
        return {"scene": "stage", "hero_score": 0.4}

    monkeypatch.setattr(vision, "analyze_one", fake_analyze)
    vision.look_at_album(conn, aid)
    assert seen["theme"] == "event"


def test_arrange_album_passes_theme_to_model(conn, monkeypatch):
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    aid = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id, gallery_theme) "
        "VALUES ('a', '/x', ?, 'general')",
        (uid,),
    ).lastrowid
    conn.execute(
        "INSERT INTO photos (album_id, scene, hero_score, storage_key, width, height) "
        "VALUES (?, 's', 0.5, 'k', 100, 80)",
        (aid,),
    )
    conn.commit()
    seen: dict[str, str] = {}

    def fake_ask(photos, *, theme):
        seen["theme"] = theme
        return None, None

    monkeypatch.setattr(arrange, "_ask_model", fake_ask)
    arrange.arrange_album(conn, aid)
    assert seen["theme"] == "general"