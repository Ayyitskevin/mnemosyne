"""Tests for the layout engine — they encode *why* the geometry matters, not just
that a function returns something: portraits must not be forced into wide slots
(and vice versa), the hero must dominate, and every photo must be placed exactly
once or the album silently drops a shot."""
from __future__ import annotations

from mnemosyne import layout


def _photo(pid: int, orient: str) -> dict:
    # A portrait is taller than wide; a landscape is wider than tall.
    return {"id": pid, "width": (800 if orient == "P" else 1200),
            "height": (1200 if orient == "P" else 800)}


def _areas_used(plan: dict) -> set[str]:
    return {slot["area"] for slot in plan["slots"]}


def test_single_photo_goes_full_bleed():
    plan = layout.plan_spread([_photo(1, "L")], 1)
    assert len(plan["slots"]) == 1
    assert plan["areas"] == '"a"'
    assert plan["slots"][0]["is_hero"] is True


def test_two_portraits_sit_side_by_side():
    # Side-by-side keeps both tall; stacking would crop portraits to wide strips.
    plan = layout.plan_spread([_photo(1, "P"), _photo(2, "P")], 1)
    assert plan["areas"] == '"a b"'


def test_two_landscapes_stack():
    # Stacking keeps both wide; side-by-side would squeeze landscapes to slivers.
    plan = layout.plan_spread([_photo(1, "L"), _photo(2, "L")], 1)
    assert plan["areas"] == '"a" "b"'


def test_landscape_hero_spans_wide_on_top():
    photos = [_photo(1, "L"), _photo(2, "P"), _photo(3, "P")]
    plan = layout.plan_spread(photos, 1)  # hero is the landscape
    assert plan["areas"] == '"a a" "b c"'


def test_portrait_hero_runs_tall_down_one_page():
    photos = [_photo(1, "P"), _photo(2, "L"), _photo(3, "L")]
    plan = layout.plan_spread(photos, 1)  # hero is the portrait
    assert plan["areas"] == '"a b" "a c"'


def test_hero_takes_area_a_regardless_of_slot_order():
    # The hero is the 3rd photo in slot order but must still get the dominant area.
    photos = [_photo(1, "L"), _photo(2, "L"), _photo(3, "P")]
    plan = layout.plan_spread(photos, 3)
    hero_slot = next(s for s in plan["slots"] if s["is_hero"])
    assert hero_slot["photo"]["id"] == 3
    assert hero_slot["area"] == "a"


def test_every_photo_placed_exactly_once():
    for n in range(1, 5):
        photos = [_photo(i, "P" if i % 2 else "L") for i in range(1, n + 1)]
        plan = layout.plan_spread(photos, 1)
        placed_ids = sorted(s["photo"]["id"] for s in plan["slots"])
        assert placed_ids == list(range(1, n + 1))
        # No two photos share a grid area.
        assert len(_areas_used(plan)) == n


def test_overfull_spread_is_clamped_not_crashed():
    # arrange caps at 4, but the renderer must never explode on a stray 5th.
    photos = [_photo(i, "L") for i in range(1, 6)]
    plan = layout.plan_spread(photos, 1)
    assert plan["slots"][0]["is_hero"] is True
    assert plan["slots"][0]["area"] == "a"
