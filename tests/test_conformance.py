"""Conformance — emitted layouts validate against the proposal schema AND the
deterministic validator (no omit/dup/misassign), on representative fixtures.

This is the retire-readiness / correctness backstop: whatever the layout path
(deterministic engine, eval fixtures, or the Mise-style baseline), the output is a
strict, schema-valid proposal that places every referenced asset exactly once with no
slot collision and no reference to a non-eligible photo — the exact thing Mise's
validator re-checks. All of it runs offline and deterministically.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from mnemosyne import arrange, baseline, config, db, evaluate, proposal
from mnemosyne.main import app

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs" / "proposal.schema.json").read_text()
)


def _schema_valid(prop: dict) -> None:
    """Raises jsonschema.ValidationError if the proposal violates the contract schema."""
    jsonschema.validate(instance=prop, schema=SCHEMA)


# --- a real DB album, laid out by the deterministic engine -------------------


@pytest.fixture
def album(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    c.execute(
        "INSERT INTO albums (id, name, source_dir, gallery_theme) "
        "VALUES (1, 'g', '/x', 'wedding')"
    )
    rows = [
        (1, "getting ready detail", 0.30, 0.80),
        (2, "wide ceremony establishing shot", 0.55, 0.70),
        (3, "couple portrait", 0.92, 0.95),
        (4, "family group", 0.78, 0.82),
        (5, "reception candids", 0.40, 0.60),
        (6, "first dance", 0.66, 0.68),
        (7, "send-off moment", 0.83, 0.86),
    ]
    for pid, scene, hero, keeper in rows:
        c.execute(
            "INSERT INTO photos (id, album_id, storage_key, width, height, scene, "
            "hero_score, keeper_score) VALUES (?, 1, ?, 1200, 800, ?, ?, ?)",
            (pid, f"a1/{pid}.jpg", scene, hero, keeper),
        )
    c.commit()
    return c


def test_engine_album_proposal_is_schema_and_validator_conformant(album, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(album, 1)

    prop = proposal.build_proposal(album, 1)
    _schema_valid(prop)
    assert proposal.validate_proposal(prop, proposal.eligible_asset_ids(album, 1)) == []
    # Every eligible photo placed exactly once (no omit / no dup) for the no-cull path.
    placed = sorted(p["asset_id"] for p in prop["placements"])
    assert placed == [1, 2, 3, 4, 5, 6, 7]


def test_engine_layout_is_reproducible(album, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(album, 1)
    first = proposal.build_proposal(album, 1)
    arrange.arrange_album(album, 1)   # re-run replaces the cached layout
    assert proposal.build_proposal(album, 1) == first


def test_db_layout_places_every_photo_once_no_collisions(album, monkeypatch):
    monkeypatch.setattr(config, "ARRANGE_BACKEND", "deterministic")
    arrange.arrange_album(album, 1)

    placements = album.execute(
        "SELECT s.position AS position, pl.slot AS slot, pl.photo_id AS pid "
        "FROM placements pl JOIN spreads s ON s.id = pl.spread_id "
        "WHERE s.album_id = 1"
    ).fetchall()
    photo_ids = [r["pid"] for r in placements]
    assert sorted(photo_ids) == [1, 2, 3, 4, 5, 6, 7]      # no omit, no dup
    slots = [(r["position"], r["slot"]) for r in placements]
    assert len(slots) == len(set(slots))                    # no (spread, slot) collision
    # Each spread's hero is actually placed on that spread (no misassigned hero).
    for s in album.execute("SELECT id, hero_photo_id FROM spreads WHERE album_id = 1"):
        on_spread = {
            r["photo_id"]
            for r in album.execute(
                "SELECT photo_id FROM placements WHERE spread_id = ?", (s["id"],)
            )
        }
        assert s["hero_photo_id"] in on_spread


# --- representative fixtures (engine + baseline, with and without a cull) -----


@pytest.mark.parametrize("name", list(evaluate.fixture_galleries()))
@pytest.mark.parametrize("keeper_floor", [0.0, 0.4])
def test_fixture_proposals_are_schema_and_validator_conformant(name, keeper_floor):
    photos = evaluate.fixture_galleries()[name]
    eligible = {p["id"] for p in photos}
    comp = evaluate.compare(photos, theme=name, keeper_floor=keeper_floor)

    for side in ("baseline", "mnemosyne"):
        prop = comp[side]["proposal"]
        _schema_valid(prop)
        assert proposal.validate_proposal(prop, eligible) == [], (name, side, keeper_floor)

    # A cull omits eligible photos but never hides them: placed + omitted partition
    # the gallery exactly, and nothing is placed twice.
    placed = [p["asset_id"] for p in comp["mnemosyne"]["proposal"]["placements"]]
    omitted = {o["id"] for o in comp["mnemosyne"]["omitted"]}
    assert len(placed) == len(set(placed))
    assert set(placed) | omitted == eligible
    assert set(placed) & omitted == set()


def test_baseline_covers_everything_and_conforms():
    photos = evaluate.fixture_galleries()["food"]
    prop = evaluate.proposal_of(
        baseline.baseline_layout(photos), provider="mise-baseline", model="deterministic"
    )
    _schema_valid(prop)
    eligible = {p["id"] for p in photos}
    assert proposal.validate_proposal(prop, eligible) == []
    assert sorted(p["asset_id"] for p in prop["placements"]) == sorted(eligible)


# --- the validator actually catches the three failure modes ------------------


def _valid_proposal() -> dict:
    return {
        "placements": [
            {"asset_id": 1, "spread": 0, "slot": 0},
            {"asset_id": 2, "spread": 0, "slot": 1},
        ],
        "provider": "deterministic",
        "model": "deterministic-v1",
    }


def test_validator_catches_duplicate_collision_and_misassign():
    eligible = {1, 2}
    # duplicate asset (an asset placed twice)
    dup = _valid_proposal()
    dup["placements"][1]["asset_id"] = 1
    assert any("more than once" in e for e in proposal.validate_proposal(dup, eligible))
    # (spread, slot) collision
    collide = _valid_proposal()
    collide["placements"][1]["slot"] = 0
    assert any("used more than once" in e for e in proposal.validate_proposal(collide, eligible))
    # misassign: references a photo that doesn't belong to this gallery
    misassign = _valid_proposal()
    misassign["placements"][1]["asset_id"] = 999
    assert any("not an eligible" in e for e in proposal.validate_proposal(misassign, eligible))


# --- /healthz is exposed ------------------------------------------------------


def test_healthz_is_exposed():
    # No lifespan (TestClient not used as a context manager) → no worker/db needed;
    # /healthz still reports liveness, backends, and the storage probe.
    client = TestClient(app)
    body = client.get("/healthz").json()
    assert body["status"] in ("ok", "degraded", "error")
    assert "vision" in body["backends"] and "arrange" in body["backends"]
    assert "backend" in body["storage"]
