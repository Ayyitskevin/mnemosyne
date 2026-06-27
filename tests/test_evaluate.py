"""Tests for the beat-the-baseline harness.

The harness's credibility rests on two things: both layouts it emits are VALID
proposals (so a Mnemosyne win is never bought with a correctness violation), and the
comparison is deterministic and surfaces the cull. These tests also assert Mnemosyne
actually differs from the baseline on the quality axes (pacing varies; weak shots are
culled and surfaced) on the fixture galleries.
"""
from __future__ import annotations

from mnemosyne import evaluate, proposal


def _photo(pid: int, hero: float, keeper: float) -> dict:
    return {"id": pid, "hero_potential": hero, "keeper_score": keeper,
            "width": 1200, "height": 800}


def _gallery() -> list[dict]:
    return [
        _photo(1, 0.92, 0.95),  # standout
        _photo(2, 0.10, 0.12),  # cull
        _photo(3, 0.55, 0.60),
        _photo(4, 0.80, 0.82),
        _photo(5, 0.15, 0.18),  # cull
        _photo(6, 0.60, 0.62),
    ]


# --- proposal shaping + validity --------------------------------------------


def test_proposal_of_densifies_and_validates():
    spreads = [{"photos": [3, 7], "hero": 3}, {"photos": [9], "hero": 9}]
    prop = evaluate.proposal_of(spreads, provider="x", model="y")
    assert prop["placements"] == [
        {"asset_id": 3, "spread": 0, "slot": 0},
        {"asset_id": 7, "spread": 0, "slot": 1},
        {"asset_id": 9, "spread": 1, "slot": 0},
    ]
    assert proposal.validate_proposal(prop, {3, 7, 9}) == []


def test_both_layouts_are_valid_proposals():
    photos = _gallery()
    comp = evaluate.compare(photos, keeper_floor=0.3)
    assert comp["baseline"]["scorecard"]["valid"] is True
    assert comp["mnemosyne"]["scorecard"]["valid"] is True
    # Validate again independently against the eligible set.
    eligible = {p["id"] for p in photos}
    assert proposal.validate_proposal(comp["baseline"]["proposal"], eligible) == []
    assert proposal.validate_proposal(comp["mnemosyne"]["proposal"], eligible) == []


# --- the comparison shows a real difference ----------------------------------


def test_mnemosyne_culls_weak_shots_and_surfaces_them():
    comp = evaluate.compare(_gallery(), keeper_floor=0.3)
    base = comp["baseline"]["scorecard"]
    mnem = comp["mnemosyne"]["scorecard"]
    assert base["omitted"] == 0 and base["coverage_pct"] == 100.0   # baseline keeps all
    assert mnem["omitted"] == 2                                     # the two weak shots
    # The cull is surfaced in the proposal notes and the omitted list, not hidden.
    assert "culled 2" in comp["mnemosyne"]["proposal"]["notes"]
    assert {o["id"] for o in comp["mnemosyne"]["omitted"]} == {2, 5}


def test_mnemosyne_varies_pacing_and_leads_with_the_best_hero():
    comp = evaluate.compare(_gallery(), keeper_floor=0.3, solo_hero_floor=0.85)
    base = comp["baseline"]["scorecard"]
    mnem = comp["mnemosyne"]["scorecard"]
    # The baseline never gives a hero its own spread (flat N + a remainder);
    # Mnemosyne deliberately paces a standout hero onto a solo spread.
    assert base["solo_hero_spreads"] == 0
    assert mnem["solo_hero_spreads"] >= 1
    assert mnem["best_hero_is_cover"] is True   # the standout opens the album


def test_placed_plus_omitted_equals_eligible():
    photos = _gallery()
    comp = evaluate.compare(photos, keeper_floor=0.3)
    placed = {p["asset_id"] for p in comp["mnemosyne"]["proposal"]["placements"]}
    omitted = {o["id"] for o in comp["mnemosyne"]["omitted"]}
    assert placed | omitted == {p["id"] for p in photos}
    assert placed & omitted == set()


# --- checklist + acceptance --------------------------------------------------


def test_checklist_has_a_row_per_placement_with_rationale():
    comp = evaluate.compare(_gallery(), keeper_floor=0.3)
    rows = comp["checklist"]
    placed = comp["mnemosyne"]["proposal"]["placements"]
    assert len(rows) == len(placed)
    assert all(r["rationale"] and r["accept"] is None for r in rows)
    assert any("cover hero" in r["rationale"] for r in rows)


def test_compute_acceptance_targets_70_percent():
    comp = evaluate.compare(_gallery(), keeper_floor=0.3)
    checklist = comp["checklist"]
    for i, r in enumerate(checklist):
        r["accept"] = i % 10 != 0    # accept 90%
    result = evaluate.compute_acceptance(checklist)
    assert result["acceptance_pct"] >= 70.0
    assert result["passes"] is True


def test_compute_acceptance_unreviewed_is_none():
    comp = evaluate.compare(_gallery())
    assert evaluate.compute_acceptance(comp["checklist"])["passes"] is None


# --- rendering + determinism + fixtures --------------------------------------


def test_render_markdown_has_the_key_sections():
    md = evaluate.render_markdown(evaluate.compare(_gallery(), keeper_floor=0.3))
    assert "| metric | baseline | mnemosyne |" in md
    assert "Surfaced cull" in md
    assert "review checklist" in md


def test_compare_is_deterministic():
    photos = _gallery()
    assert evaluate.compare(photos, keeper_floor=0.3) == evaluate.compare(photos, keeper_floor=0.3)


def test_fixtures_produce_valid_comparisons():
    for name, photos in evaluate.fixture_galleries().items():
        comp = evaluate.compare(photos, keeper_floor=0.3)
        assert comp["baseline"]["scorecard"]["valid"] is True, name
        assert comp["mnemosyne"]["scorecard"]["valid"] is True, name
