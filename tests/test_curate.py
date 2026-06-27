"""Tests for the deterministic, signal-driven curation core.

These encode the layout-quality promises AND the correctness guardrail: culls favor
keepers and are SURFACED (never silent), the selected + omitted sets partition the
gallery exactly, a real gallery is never culled to empty, the sequence has a strong
opener and closer, pacing varies (solo spreads for standout heroes), and the whole
thing is reproducible.
"""
from __future__ import annotations

import random

from mnemosyne import curate


def _photo(pid: int, hero: float, keeper: float | None = None) -> dict:
    p = {"id": pid, "hero_potential": hero}
    if keeper is not None:
        p["keeper_score"] = keeper
    return p


def _ids(photos: list[dict]) -> set[int]:
    return {p["id"] for p in photos}


# --- selection / cull --------------------------------------------------------


def test_cull_drops_below_floor_and_surfaces_them():
    photos = [_photo(1, 0.9, 0.9), _photo(2, 0.1, 0.1), _photo(3, 0.5, 0.5)]
    selected, omitted = curate.select_and_sequence(photos, keeper_floor=0.4)
    assert _ids(selected) == {1, 3}
    assert [o["id"] for o in omitted] == [2]
    assert "below floor" in omitted[0]["reason"]


def test_selected_and_omitted_partition_the_gallery():
    photos = [_photo(p, hero=(p % 5) / 5, keeper=(p % 4) / 4) for p in range(1, 12)]
    selected, omitted = curate.select_and_sequence(photos, keeper_floor=0.3)
    placed, dropped = _ids(selected), {o["id"] for o in omitted}
    assert placed | dropped == _ids(photos)   # cover everything
    assert placed & dropped == set()           # no photo both placed and omitted


def test_never_culls_a_real_gallery_to_empty():
    photos = [_photo(1, 0.1, 0.1), _photo(2, 0.2, 0.2)]
    selected, omitted = curate.select_and_sequence(photos, keeper_floor=0.9)
    assert len(selected) == 1                   # the single strongest survives
    assert selected[0]["id"] == 2
    assert {o["id"] for o in omitted} == {1}    # the other is surfaced, not lost


def test_target_count_caps_and_surfaces_overflow():
    photos = [_photo(p, hero=p / 10, keeper=p / 10) for p in range(1, 7)]
    selected, omitted = curate.select_and_sequence(photos, target_count=3)
    assert len(selected) == 3
    assert {o["id"] for o in omitted} == {1, 2, 3}   # weakest keepers dropped
    assert all("target album size" in o["reason"] for o in omitted)


# --- sequencing --------------------------------------------------------------


def test_opener_is_best_hero_and_closer_is_next_best():
    photos = [_photo(1, 0.5), _photo(2, 0.95), _photo(3, 0.2), _photo(4, 0.8)]
    selected, _ = curate.select_and_sequence(photos)
    assert selected[0]["id"] == 2    # best hero opens
    assert selected[-1]["id"] == 4   # next-best hero closes
    assert [p["id"] for p in selected[1:-1]] == [1, 3]  # middle stays chronological


def test_single_photo_sequence():
    selected, _ = curate.select_and_sequence([_photo(7, 0.5)])
    assert [p["id"] for p in selected] == [7]


# --- pacing / grouping -------------------------------------------------------


def test_standout_hero_gets_a_solo_spread():
    selected = [_photo(1, 0.5), _photo(2, 0.95), _photo(3, 0.5)]
    spreads = curate.group_into_spreads(selected, solo_hero_floor=0.85, max_per_spread=4)
    solos = [s for s in spreads if len(s["photos"]) == 1]
    assert any(s["photos"] == [2] and s["hero"] == 2 for s in solos)


def test_grouping_packs_to_max_and_places_each_once():
    selected = [_photo(p, 0.5) for p in range(1, 8)]
    spreads = curate.group_into_spreads(selected, max_per_spread=3)
    assert all(len(s["photos"]) <= 3 for s in spreads)
    placed = [pid for s in spreads for pid in s["photos"]]
    assert placed == list(range(1, 8))          # order preserved, all once


def test_grouping_preserves_sequence_order():
    selected = [_photo(p, 0.5) for p in (5, 1, 3)]
    spreads = curate.group_into_spreads(selected, max_per_spread=4)
    assert [pid for s in spreads for pid in s["photos"]] == [5, 1, 3]


# --- composed + reproducible -------------------------------------------------


def test_mnemosyne_layout_is_deterministic():
    photos = [_photo(p, hero=(p * 7 % 10) / 10, keeper=(p * 3 % 10) / 10) for p in range(1, 15)]
    a = curate.mnemosyne_layout(photos, keeper_floor=0.2)
    b = curate.mnemosyne_layout(photos, keeper_floor=0.2)
    assert a == b


def test_layout_partitions_eligible_for_random_galleries():
    rng = random.Random(20260627)
    for _ in range(40):
        n = rng.randint(1, 30)
        photos = [
            _photo(i, hero=round(rng.random(), 3), keeper=round(rng.random(), 3))
            for i in range(1, n + 1)
        ]
        layout = curate.mnemosyne_layout(photos, keeper_floor=0.3)
        placed = [pid for s in layout["spreads"] for pid in s["photos"]]
        dropped = {o["id"] for o in layout["omitted"]}
        # Every eligible photo is placed exactly once OR surfaced as omitted.
        assert len(placed) == len(set(placed))            # no duplicates
        assert set(placed) | dropped == _ids(photos)      # nothing lost
        assert set(placed) & dropped == set()             # nothing both
        # Each spread's hero is actually on that spread.
        for s in layout["spreads"]:
            assert s["hero"] in s["photos"]


def test_keeper_absent_falls_back_to_hero():
    # No keeper_score at all → ranking/cull use hero, nothing wrongly dropped.
    photos = [_photo(1, 0.9), _photo(2, 0.1)]
    selected, omitted = curate.select_and_sequence(photos, keeper_floor=0.5)
    assert _ids(selected) == {1}
    assert {o["id"] for o in omitted} == {2}


# --- narrative-arc sequencing ------------------------------------------------


def _sp(pid: int, hero: float, scene: str, keeper: float = 0.9) -> dict:
    return {"id": pid, "hero_potential": hero, "keeper_score": keeper, "scene": scene}


def test_middle_is_ordered_by_the_theme_arc_not_capture_order():
    # Scrambled capture ids; the body should reorder along the wedding arc while the
    # best hero covers and the strongest remaining hero closes.
    photos = [
        _sp(1, 0.2, "getting ready detail"),
        _sp(2, 0.2, "reception candids"),
        _sp(3, 0.2, "wide ceremony establishing shot"),
        _sp(4, 0.65, "couple portrait"),    # best hero -> cover
        _sp(5, 0.5, "family group"),        # 2nd hero -> closer
    ]
    selected, _ = curate.select_and_sequence(photos, theme="wedding")
    # cover, [getting-ready -> ceremony -> reception], closer
    assert [p["id"] for p in selected] == [4, 1, 3, 2, 5]


def test_unlabelled_middle_stays_chronological():
    # Without scene labels the arc can't classify, so the body falls back to id order.
    # Distinct cover (0.9) and closer (0.5) so the middle three are unambiguous.
    photos = [_photo(p, 0.3, 0.9) for p in (1, 2, 3)] + [
        _photo(8, 0.5, 0.9), _photo(9, 0.9, 0.9)
    ]
    ids = [p["id"] for p in curate.select_and_sequence(photos)[0]]
    assert ids[0] == 9            # best hero covers
    assert ids[-1] == 8           # next-best hero closes
    assert ids[1:-1] == [1, 2, 3]  # the unlabelled middle stays chronological


# --- variable-pacing (anchor tier) -------------------------------------------


def test_anchor_hero_leads_a_smaller_spread_than_filler():
    selected = [
        _photo(1, 0.75),  # anchor (>= 0.7) -> caps its spread at anchor_max
        _photo(2, 0.4),
        _photo(3, 0.4),
        _photo(4, 0.4),
        _photo(5, 0.4),
    ]
    spreads = curate.group_into_spreads(
        selected, solo_hero_floor=0.85, anchor_hero_floor=0.7, anchor_max=2,
        max_per_spread=4,
    )
    # First spread is the anchor's, capped at 2 (the anchor + one supporting shot);
    # the filler then packs into a larger spread — varied pacing, not a flat N.
    assert spreads[0]["photos"] == [1, 2] and spreads[0]["hero"] == 1
    assert max(len(s["photos"]) for s in spreads) > 2


def test_three_pacing_tiers_coexist_and_place_each_once():
    selected = [
        _photo(1, 0.95),  # solo
        _photo(2, 0.75),  # anchor
        _photo(3, 0.4),
        _photo(4, 0.4),
        _photo(5, 0.4),
        _photo(6, 0.4),
    ]
    spreads = curate.group_into_spreads(selected)
    sizes = sorted(len(s["photos"]) for s in spreads)
    assert 1 in sizes and max(sizes) >= 3          # a solo and a packed spread exist
    placed = [pid for s in spreads for pid in s["photos"]]
    assert sorted(placed) == [1, 2, 3, 4, 5, 6]    # everything placed once
    assert spreads[0]["photos"] == [1]             # the standout opens, full-bleed
