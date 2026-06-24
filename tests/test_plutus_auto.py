"""Auto Plutus offer attachment when an album reaches ready."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from mnemosyne import arrange, auth, config, db, plutus_api, plutus_auto, vision, worker
from mnemosyne.worker import _run_one


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


def _ready_album(conn, tmp_path, uid: int, *, run_id: int | None = 99) -> int:
    gal = tmp_path / "gal"
    gal.mkdir()
    Image.new("RGB", (8, 8)).save(gal / "01.jpg", "JPEG")
    aid = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id, status, plutus_run_id) "
        "VALUES ('Test', ?, ?, 'processing', ?)",
        (str(gal), uid, run_id),
    ).lastrowid
    conn.execute(
        "UPDATE albums SET claim_token = 'tok' WHERE id = ?", (aid,)
    )
    conn.commit()
    return aid


def test_maybe_attach_offer_skips_when_disabled(conn, monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_AUTO_LINK", False)
    monkeypatch.setattr(plutus_api, "create_offer_url", lambda **kw: (_ for _ in ()).throw(
        AssertionError("should not call")
    ))
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    aid = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id, plutus_run_id) "
        "VALUES ('a', '/x', ?, 1)",
        (uid,),
    ).lastrowid
    conn.commit()
    assert plutus_auto.maybe_attach_offer(conn, aid) is None


def test_maybe_attach_offer_mints_and_persists(conn, tmp_path, monkeypatch):
    offer = "https://plutus.example.com/store/demo/offer/auto"
    monkeypatch.setattr(config, "PLUTUS_AUTO_LINK", True)
    monkeypatch.setattr(config, "PLUTUS_URL", "https://plutus.example.com")
    monkeypatch.setattr(config, "PLUTUS_API_TOKEN", "tok")
    monkeypatch.setattr(config, "PLUTUS_TENANT_ID", "flow-studio")
    monkeypatch.setattr(
        plutus_api,
        "create_offer_url",
        lambda **kw: offer if kw.get("run_id") == 99 else (_ for _ in ()).throw(
            AssertionError("wrong run")
        ),
    )

    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    aid = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id, plutus_run_id) "
        "VALUES ('My book', '/x', ?, 99)",
        (uid,),
    ).lastrowid
    conn.commit()

    assert plutus_auto.maybe_attach_offer(conn, aid) == offer
    row = conn.execute(
        "SELECT plutus_offer_url FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    assert row["plutus_offer_url"] == offer


def test_worker_attaches_offer_on_ready(conn, tmp_path, monkeypatch, no_models):
    offer = "https://plutus.example.com/store/demo/offer/worker"
    monkeypatch.setattr(config, "PLUTUS_AUTO_LINK", True)
    monkeypatch.setattr(config, "PLUTUS_URL", "https://plutus.example.com")
    monkeypatch.setattr(config, "PLUTUS_API_TOKEN", "tok")
    monkeypatch.setattr(config, "PLUTUS_TENANT_ID", "flow-studio")
    monkeypatch.setattr(plutus_api, "create_offer_url", lambda **kw: offer)

    uid = auth.create_user(conn, "w@example.com", "pw12345")["id"]
    aid = _ready_album(conn, tmp_path, uid, run_id=12)
    _run_one(conn, aid, "tok")

    row = conn.execute(
        "SELECT status, plutus_offer_url FROM albums WHERE id = ?", (aid,)
    ).fetchone()
    assert row["status"] == "ready"
    assert row["plutus_offer_url"] == offer