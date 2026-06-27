"""Tests for consuming Mise's per-photo signals — the contract's 'use Mise's
scores, don't recompute vision' rule.

These encode why the import reads signals the way it does: Mise's hero/keeper scores
are adopted onto the photo rows so the look step skips them (no recompute), a Mise
asset id flows into the proposal so a placement references Mise's id space, and ANY
Mise hiccup falls back to local vision rather than failing the build.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from mnemosyne import (
    arrange,
    auth,
    config,
    db,
    ingest,
    mise_client,
    mise_import,
    pipeline,
    proposal,
    vision,
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def _mise_album(c: sqlite3.Connection, *, gallery_id: int | None, names: list[str]) -> int:
    cur = c.execute(
        "INSERT INTO albums (name, source_dir, mise_gallery_id) VALUES ('g', '/x', ?)",
        (gallery_id,),
    )
    aid = cur.lastrowid
    for name in names:
        c.execute(
            "INSERT INTO photos (album_id, storage_key, width, height) "
            "VALUES (?, ?, 1200, 800)",
            (aid, f"a{aid}/{name}"),
        )
    c.commit()
    return aid


# --- _normalize_asset --------------------------------------------------------


def test_normalize_flat_fields():
    a = mise_client._normalize_asset(
        {"id": 7, "filename": "a.jpg", "hero_potential": 0.8,
         "keeper_score": 0.6, "scene": "hero dish", "processed": True}
    )
    assert a == {
        "asset_id": 7, "filename": "a.jpg", "hero_potential": 0.8,
        "keeper_score": 0.6, "scene": "hero dish", "processed": True,
    }


def test_normalize_nested_culling_and_shot_type():
    # Argus's shape: signals under `culling`, label from shot_type + first keyword.
    a = mise_client._normalize_asset(
        {"asset_id": 3, "name": "b.jpg", "shot_type": "overhead",
         "keywords": ["plated", "pasta"], "culling": {"hero_potential": 1.5,
         "keeper_score": -2}}
    )
    assert a["asset_id"] == 3
    assert a["filename"] == "b.jpg"
    assert a["scene"] == "overhead plated"
    assert a["hero_potential"] == 1.0   # clamped into 0..1
    assert a["keeper_score"] == 0.0     # clamped into 0..1


def test_normalize_defaults_processed_true_and_derives_filename_from_path():
    a = mise_client._normalize_asset({"id": 1, "path": "/srv/g/9/original/c.jpg"})
    assert a["filename"] == "c.jpg"
    assert a["processed"] is True       # absent → treated as processed
    assert a["hero_potential"] is None  # absent stays None, not a fake 0
    assert a["scene"] is None


def test_normalize_status_ready_flag():
    assert mise_client._normalize_asset({"id": 1, "name": "x", "status": "pending"})[
        "processed"
    ] is False


def test_normalize_drops_row_with_no_id_or_filename():
    assert mise_client._normalize_asset({"hero_potential": 0.9}) is None


# --- list_assets (transport) -------------------------------------------------


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        if self._body is _BAD_JSON:
            raise ValueError("not json")
        return self._body


_BAD_JSON = object()


def _patch_client(monkeypatch, resp: _Resp, captured: dict | None = None):
    class _Inner:
        def get(self, url, headers=None):
            if captured is not None:
                captured["url"] = url
            return resp

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


def test_list_assets_accepts_assets_key(monkeypatch):
    captured: dict = {}
    _patch_client(
        monkeypatch,
        _Resp({"assets": [{"id": 1, "filename": "a.jpg", "hero_potential": 0.7}]}),
        captured,
    )
    out = mise_client.list_assets(42)
    assert len(out) == 1 and out[0]["asset_id"] == 1
    assert captured["url"] == "http://mise.test/api/galleries/42/assets"


def test_list_assets_accepts_photos_key_and_bare_list(monkeypatch):
    _patch_client(monkeypatch, _Resp({"photos": [{"id": 2, "filename": "b.jpg"}]}))
    assert mise_client.list_assets(1)[0]["asset_id"] == 2
    _patch_client(monkeypatch, _Resp([{"id": 3, "filename": "c.jpg"}]))
    assert mise_client.list_assets(1)[0]["asset_id"] == 3


def test_list_assets_404_raises(monkeypatch):
    _patch_client(monkeypatch, _Resp({}, status=404))
    with pytest.raises(mise_client.MiseClientError, match="not found"):
        mise_client.list_assets(1)


def test_list_assets_non_json_raises(monkeypatch):
    _patch_client(monkeypatch, _Resp(_BAD_JSON))
    with pytest.raises(mise_client.MiseClientError, match="non-JSON"):
        mise_client.list_assets(1)


def test_list_assets_unexpected_body_raises(monkeypatch):
    _patch_client(monkeypatch, _Resp({"nope": 1}))
    with pytest.raises(mise_client.MiseClientError, match="unexpected body"):
        mise_client.list_assets(1)


# --- apply_mise_signals ------------------------------------------------------


def test_apply_is_noop_for_non_mise_album(conn):
    aid = _mise_album(conn, gallery_id=None, names=["a.jpg"])
    assert mise_import.apply_mise_signals(conn, aid) == {
        "matched": 0, "signals_adopted": 0, "ids_only": 0
    }


def test_apply_adopts_complete_signal_and_skips_vision(conn, monkeypatch):
    aid = _mise_album(conn, gallery_id=9, names=["a.jpg", "b.jpg"])
    monkeypatch.setattr(mise_client, "configured", lambda: True)
    monkeypatch.setattr(
        mise_client, "list_assets",
        lambda gid: [
            {"asset_id": 501, "filename": "a.jpg", "hero_potential": 0.9,
             "keeper_score": 0.7, "scene": "hero dish", "processed": True},
        ],
    )
    summary = mise_import.apply_mise_signals(conn, aid)
    assert summary == {"matched": 1, "signals_adopted": 1, "ids_only": 0}

    a = conn.execute(
        "SELECT mise_asset_id, hero_score, keeper_score, scene FROM photos "
        "WHERE storage_key = ?", (f"a{aid}/a.jpg",)
    ).fetchone()
    assert (a["mise_asset_id"], a["hero_score"], a["keeper_score"], a["scene"]) == (
        501, 0.9, 0.7, "hero dish"
    )
    # b.jpg had no Mise asset → untouched, scene NULL → the look step will score it.
    b = conn.execute(
        "SELECT scene FROM photos WHERE storage_key = ?", (f"a{aid}/b.jpg",)
    ).fetchone()
    assert b["scene"] is None


def test_apply_partial_signal_sets_id_only_leaves_scene_for_vision(conn, monkeypatch):
    aid = _mise_album(conn, gallery_id=9, names=["a.jpg"])
    monkeypatch.setattr(mise_client, "configured", lambda: True)
    # hero_potential missing → incomplete → carry id + keeper, but DON'T set hero/scene.
    monkeypatch.setattr(
        mise_client, "list_assets",
        lambda gid: [{"asset_id": 77, "filename": "a.jpg", "keeper_score": 0.4}],
    )
    summary = mise_import.apply_mise_signals(conn, aid)
    assert summary == {"matched": 1, "signals_adopted": 0, "ids_only": 1}
    a = conn.execute(
        "SELECT mise_asset_id, hero_score, keeper_score, scene FROM photos "
        "WHERE album_id = ?", (aid,)
    ).fetchone()
    assert a["mise_asset_id"] == 77
    assert a["keeper_score"] == 0.4
    assert a["hero_score"] is None   # not half-set: vision will fill hero + scene
    assert a["scene"] is None


def test_apply_swallows_mise_failure(conn, monkeypatch):
    aid = _mise_album(conn, gallery_id=9, names=["a.jpg"])
    monkeypatch.setattr(mise_client, "configured", lambda: True)

    def _boom(gid):
        raise mise_client.MiseClientError("mise down")

    monkeypatch.setattr(mise_client, "list_assets", _boom)
    # Must not raise — the build falls back to local vision.
    assert mise_import.apply_mise_signals(conn, aid) == {
        "matched": 0, "signals_adopted": 0, "ids_only": 0
    }
    a = conn.execute(
        "SELECT scene, mise_asset_id FROM photos WHERE album_id = ?", (aid,)
    ).fetchone()
    assert a["scene"] is None and a["mise_asset_id"] is None


# --- proposal references Mise's id -------------------------------------------


def test_proposal_reports_mise_asset_id_when_present(conn, monkeypatch):
    aid = _mise_album(conn, gallery_id=9, names=["a.jpg", "b.jpg"])
    monkeypatch.setattr(mise_client, "configured", lambda: True)
    monkeypatch.setattr(
        mise_client, "list_assets",
        lambda gid: [
            {"asset_id": 900, "filename": "a.jpg", "hero_potential": 0.9, "scene": "x"},
            {"asset_id": 901, "filename": "b.jpg", "hero_potential": 0.5, "scene": "y"},
        ],
    )
    mise_import.apply_mise_signals(conn, aid)
    # Lay them out on one spread so the proposal has placements.
    sid = conn.execute(
        "INSERT INTO spreads (album_id, position, hero_photo_id) VALUES (?, 1, NULL)",
        (aid,),
    ).lastrowid
    for slot, name in enumerate(("a.jpg", "b.jpg"), start=1):
        pid = conn.execute(
            "SELECT id FROM photos WHERE storage_key = ?", (f"a{aid}/{name}",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO placements (spread_id, photo_id, slot) VALUES (?, ?, ?)",
            (sid, pid, slot),
        )
    conn.commit()

    out = proposal.build_proposal(conn, aid)
    asset_ids = sorted(p["asset_id"] for p in out["placements"])
    assert asset_ids == [900, 901]  # Mise's ids, not the local photo ids
    assert proposal.eligible_asset_ids(conn, aid) == {900, 901}
    assert proposal.validate_proposal(out, {900, 901}) == []


def test_proposal_falls_back_to_local_id_without_mise_asset(conn):
    # No Mise mapping → COALESCE yields the local photo id, unchanged behavior.
    aid = _mise_album(conn, gallery_id=None, names=["a.jpg"])
    pid = conn.execute(
        "SELECT id FROM photos WHERE album_id = ?", (aid,)
    ).fetchone()["id"]
    conn.execute("UPDATE photos SET scene = 'x' WHERE id = ?", (pid,))
    sid = conn.execute(
        "INSERT INTO spreads (album_id, position) VALUES (?, 1)", (aid,)
    ).lastrowid
    conn.execute(
        "INSERT INTO placements (spread_id, photo_id, slot) VALUES (?, ?, 1)",
        (sid, pid),
    )
    conn.commit()
    out = proposal.build_proposal(conn, aid)
    assert out["placements"][0]["asset_id"] == pid


# --- end-to-end: process_album consumes the signal, vision isn't recomputed ---


def test_process_album_skips_vision_for_mise_scored_photo(conn, tmp_path, monkeypatch):
    src = tmp_path / "gal"
    src.mkdir()
    for name in ("a.jpg", "b.jpg"):
        Image.new("RGB", (10, 8), (40, 0, 0)).save(src / name, "JPEG")

    uid = auth.create_user(conn, "u@example.com", "pw12345")["id"]
    aid = ingest.create_album(
        conn, name="G", source_dir=src, owner_id=uid, status="pending",
        mise_gallery_id=9,
    )

    monkeypatch.setattr(mise_client, "configured", lambda: True)
    monkeypatch.setattr(
        mise_client, "list_assets",
        lambda gid: [
            {"asset_id": 800, "filename": "a.jpg", "hero_potential": 0.95,
             "keeper_score": 0.8, "scene": "mise hero"},
        ],
    )
    # Count look-step calls: only b.jpg (no Mise signal) should hit vision.
    calls: list[str] = []

    def _fake_vision(path, **kw):
        calls.append(Path(path).name)
        return {"scene": "local", "hero_score": 0.3}

    monkeypatch.setattr(vision, "analyze_one", _fake_vision)
    monkeypatch.setattr(arrange, "_ask_model", lambda photos, **kw: (None, None))

    pipeline.process_album(conn, aid)

    # a.jpg kept Mise's score and was NOT sent to vision; b.jpg was scored locally.
    assert calls == ["b.jpg"]
    a = conn.execute(
        "SELECT mise_asset_id, hero_score, keeper_score, scene FROM photos "
        "WHERE storage_key = ?", (f"a{aid}/a.jpg",)
    ).fetchone()
    assert (a["mise_asset_id"], a["hero_score"], a["scene"]) == (800, 0.95, "mise hero")
    assert a["keeper_score"] == 0.8

    # Partial Mise match (a.jpg mapped, b.jpg not) → the proposal stays in ONE id
    # space: the local ids, collision-free and valid. (Full match → Mise ids is
    # covered by test_proposal_reports_mise_asset_id_when_present.)
    out = proposal.build_proposal(conn, aid)
    eligible = proposal.eligible_asset_ids(conn, aid)
    assert proposal.validate_proposal(out, eligible) == []
    local_ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM photos WHERE album_id = ?", (aid,)
        ).fetchall()
    }
    assert {p["asset_id"] for p in out["placements"]} <= local_ids


def test_partial_match_never_mixes_id_spaces_into_a_duplicate(conn):
    # The collision trap: one photo's Mise id equals another photo's LOCAL id. A
    # naive COALESCE(mise_asset_id, id) would emit that value twice. The single-space
    # rule must fall back to local ids for the whole album instead.
    aid = _mise_album(conn, gallery_id=9, names=["a.jpg", "b.jpg"])
    rows = conn.execute(
        "SELECT id, storage_key FROM photos WHERE album_id = ? ORDER BY id", (aid,)
    ).fetchall()
    p1, p2 = rows[0]["id"], rows[1]["id"]
    # p1's Mise id is set to p2's local id; p2 stays unmapped. Both eligible.
    conn.execute(
        "UPDATE photos SET mise_asset_id = ?, scene = 'x' WHERE id = ?", (p2, p1)
    )
    conn.execute("UPDATE photos SET scene = 'y' WHERE id = ?", (p2,))
    sid = conn.execute(
        "INSERT INTO spreads (album_id, position) VALUES (?, 1)", (aid,)
    ).lastrowid
    for slot, pid in enumerate((p1, p2), start=1):
        conn.execute(
            "INSERT INTO placements (spread_id, photo_id, slot) VALUES (?, ?, ?)",
            (sid, pid, slot),
        )
    conn.commit()

    out = proposal.build_proposal(conn, aid)
    asset_ids = sorted(p["asset_id"] for p in out["placements"])
    assert asset_ids == sorted([p1, p2])          # local ids, no duplicate
    assert len(asset_ids) == len(set(asset_ids))
    assert proposal.validate_proposal(out, proposal.eligible_asset_ids(conn, aid)) == []
