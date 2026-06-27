"""Tests for the proposal contract surface — the strict JSON Mise re-validates.

These encode *why* the boundary exists: Mnemosyne must emit a proposal that
references only eligible gallery assets, places each asset exactly once, and never
collides two photos into the same (spread, slot) — because Mise rejects a malformed
proposal outright. The validator is Mnemosyne's local mirror of that rejection, so a
bad layout is caught here instead of at the gallery owner's expense.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mnemosyne import arrange, auth, config, db, pipeline, proposal, vision
from mnemosyne.main import app, get_conn

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs" / "proposal.schema.json").read_text()
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    c.execute("INSERT INTO albums (id, name, source_dir) VALUES (1, 'a', '/x')")
    # Three spreads with gappy positions (1, 5, 9) to prove the serializer densifies
    # to 0,1,2 rather than trusting the stored index.
    for sid, pos in [(10, 1), (11, 5), (12, 9)]:
        c.execute(
            "INSERT INTO spreads (id, album_id, position) VALUES (?, 1, ?)", (sid, pos)
        )
    for pid in (100, 101, 102, 103, 104):
        c.execute(
            "INSERT INTO photos (id, album_id, storage_key, width, height, scene, "
            "hero_score) VALUES (?, 1, ?, 1200, 800, 'scene', 0.5)",
            (pid, f"a1/{pid}.jpg"),
        )
    # spread 10: slots 2,4 (non-contiguous → densify to 0,1); spread 11: one photo;
    # spread 12: two photos.
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (10, 100, 2)")
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (10, 101, 4)")
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (11, 102, 1)")
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (12, 103, 1)")
    c.execute("INSERT INTO placements (spread_id, photo_id, slot) VALUES (12, 104, 2)")
    c.execute("UPDATE spreads SET hero_photo_id = 100 WHERE id = 10")
    c.commit()
    return c


# --- build_proposal ----------------------------------------------------------


def test_build_emits_contract_shape(conn):
    out = proposal.build_proposal(conn, 1)
    assert set(out) >= {"placements", "provider", "model"}
    assert isinstance(out["provider"], str) and out["provider"]
    assert isinstance(out["model"], str) and out["model"]
    assert len(out["placements"]) == 5


def test_build_densifies_spreads_and_slots_to_zero_based(conn):
    out = proposal.build_proposal(conn, 1)
    spreads = sorted({p["spread"] for p in out["placements"]})
    assert spreads == [0, 1, 2]  # stored positions were 1,5,9
    spread0_slots = sorted(p["slot"] for p in out["placements"] if p["spread"] == 0)
    assert spread0_slots == [0, 1]  # stored slots were 2,4


def test_build_places_every_asset_once_no_collisions(conn):
    out = proposal.build_proposal(conn, 1)
    asset_ids = [p["asset_id"] for p in out["placements"]]
    assert sorted(asset_ids) == [100, 101, 102, 103, 104]
    assert len(asset_ids) == len(set(asset_ids))
    slots = [(p["spread"], p["slot"]) for p in out["placements"]]
    assert len(slots) == len(set(slots))


def test_build_output_passes_its_own_validator(conn):
    out = proposal.build_proposal(conn, 1)
    eligible = proposal.eligible_asset_ids(conn, 1)
    assert proposal.validate_proposal(out, eligible) == []


def test_notes_threaded_when_given(conn):
    out = proposal.build_proposal(conn, 1, notes="culled blurry frames")
    assert out["notes"] == "culled blurry frames"


def test_empty_album_is_a_valid_empty_proposal(conn):
    conn.execute("INSERT INTO albums (id, name, source_dir) VALUES (2, 'b', '/y')")
    conn.commit()
    out = proposal.build_proposal(conn, 2)
    assert out["placements"] == []
    assert proposal.validate_proposal(out, set()) == []


def test_eligible_excludes_unscored_photos(conn):
    # A photo with no scene has not been processed by the look step → not eligible.
    conn.execute(
        "INSERT INTO photos (id, album_id, storage_key, width, height) "
        "VALUES (200, 1, 'a1/200.jpg', 1200, 800)"
    )
    conn.commit()
    assert 200 not in proposal.eligible_asset_ids(conn, 1)


# --- validate_proposal -------------------------------------------------------


def _valid() -> dict:
    return {
        "placements": [
            {"asset_id": 1, "spread": 0, "slot": 0},
            {"asset_id": 2, "spread": 0, "slot": 1},
        ],
        "provider": "ollama",
        "model": "qwen3.6:35b",
    }


def test_validator_accepts_a_clean_proposal():
    assert proposal.validate_proposal(_valid(), {1, 2}) == []


def test_validator_flags_ineligible_asset():
    errs = proposal.validate_proposal(_valid(), {1})  # 2 is not eligible
    assert any("not an eligible" in e for e in errs)


def test_validator_flags_duplicate_asset():
    p = _valid()
    p["placements"][1]["asset_id"] = 1  # 1 placed twice
    errs = proposal.validate_proposal(p, {1, 2})
    assert any("placed more than once" in e for e in errs)


def test_validator_flags_slot_collision():
    p = _valid()
    p["placements"][1]["slot"] = 0  # both at (0, 0)
    errs = proposal.validate_proposal(p, {1, 2})
    assert any("used more than once" in e for e in errs)


def test_validator_flags_negative_indices():
    p = _valid()
    p["placements"][0]["spread"] = -1
    errs = proposal.validate_proposal(p, {1, 2})
    assert any("spread must be an integer >= 0" in e for e in errs)


def test_validator_rejects_bool_as_asset_id():
    # bool is an int subclass; it must not pass as an asset_id.
    p = _valid()
    p["placements"][0]["asset_id"] = True
    errs = proposal.validate_proposal(p, {1, 2})
    assert any("asset_id must be an integer" in e for e in errs)


def test_validator_requires_provider_and_model():
    p = _valid()
    p["provider"] = ""
    del p["model"]
    errs = proposal.validate_proposal(p, {1, 2})
    assert any("provider" in e for e in errs)
    assert any("model" in e for e in errs)


def test_validator_skips_eligibility_when_none():
    # Shape-only validation: no eligible set means asset membership isn't checked.
    assert proposal.validate_proposal(_valid(), None) == []


def test_validator_rejects_non_object():
    assert proposal.validate_proposal([], {1}) == ["proposal must be a JSON object"]


# --- schema conformance ------------------------------------------------------


def test_schema_file_is_well_formed():
    assert SCHEMA["type"] == "object"
    assert set(SCHEMA["required"]) == {"placements", "provider", "model"}


def test_built_proposal_matches_schema_required_and_types(conn):
    out = proposal.build_proposal(conn, 1)
    for key in SCHEMA["required"]:
        assert key in out
    item_props = SCHEMA["properties"]["placements"]["items"]["properties"]
    for pl in out["placements"]:
        assert set(pl) == set(item_props)
        assert isinstance(pl["asset_id"], int) and not isinstance(pl["asset_id"], bool)
        assert pl["spread"] >= 0 and pl["slot"] >= 0


# --- the read-only endpoint --------------------------------------------------


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "dish", "hero_score": 0.5}
    )
    # Force the deterministic fallback so the layout is stable without a model call.
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


def _gallery(root: Path, n: int = 3) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (10, 8), (i * 40, 0, 0)).save(root / f"{i:02d}.jpg", "JPEG")
    return root


def _signup(client: TestClient, email: str) -> int:
    client.post("/signup", data={"email": email, "password": "pw12345"})
    c = db.connect(client._db_path)
    uid = c.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    c.close()
    return uid


def _ready_album(client: TestClient, uid: int, root: Path) -> int:
    c = db.connect(client._db_path)
    aid = pipeline.enqueue_album(c, name="P", source_dir=_gallery(root), owner_id=uid)
    pipeline.process_album(c, aid)
    c.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    c.commit()
    c.close()
    return aid


def test_proposal_route_returns_valid_contract(web, tmp_path):
    uid = _signup(web, "owner@example.com")
    aid = _ready_album(web, uid, tmp_path / "g")
    r = web.get(f"/albums/{aid}/proposal.json")
    assert r.status_code == 200
    body = r.json()
    assert {"placements", "provider", "model"} <= set(body)
    assert len(body["placements"]) == 3
    # Every emitted asset id is one of the album's photos, placed once, no collision.
    c = db.connect(web._db_path)
    eligible = {
        row["id"] for row in c.execute(
            "SELECT id FROM photos WHERE album_id = ?", (aid,)
        ).fetchall()
    }
    c.close()
    assert proposal.validate_proposal(body, eligible) == []


def test_proposal_route_is_owner_scoped(web, tmp_path):
    owner = _signup(web, "a@example.com")
    aid = _ready_album(web, owner, tmp_path / "g")
    # A different logged-in user must not see it — 404, not a leak.
    _signup(web, "b@example.com")
    r = web.get(f"/albums/{aid}/proposal.json")
    assert r.status_code == 404


def test_proposal_route_requires_login(web, tmp_path):
    owner = _signup(web, "a@example.com")
    aid = _ready_album(web, owner, tmp_path / "g")
    web.post("/logout")
    r = web.get(f"/albums/{aid}/proposal.json", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
