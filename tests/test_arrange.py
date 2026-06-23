"""Tests for the arrange repair pass. The reasoning model reliably gets the story
order and grouping right but routinely fumbles the exact photo ids — duplicating
one, inventing one that doesn't exist, overstuffing a spread. These encode *why*
we repair instead of reject: a single fumbled id must not throw away good
sequencing, and the result must always place every real photo exactly once so the
show station never drops or double-renders a shot."""
from __future__ import annotations

from mnemosyne import arrange


def _photo(pid: int, score: float = 0.5) -> dict:
    return {"id": pid, "scene": f"s{pid}", "hero_score": score,
            "width": 1200, "height": 800}


def _placed(spreads: list[dict]) -> list[int]:
    out: list[int] = []
    for s in spreads:
        out.extend(s["photos"])
    return out


def test_duplicate_id_is_dropped_not_double_placed():
    photos = [_photo(i) for i in range(1, 4)]
    # Model put photo 2 on two different spreads.
    spreads = [{"photos": [1, 2], "hero": 1}, {"photos": [2, 3], "hero": 3}]
    out = arrange._repair(spreads, photos)
    assert sorted(_placed(out)) == [1, 2, 3]


def test_hallucinated_id_is_dropped():
    photos = [_photo(i) for i in range(1, 4)]
    # Photo 99 does not exist; the empty leftover spread must vanish.
    spreads = [{"photos": [1, 2, 3], "hero": 1}, {"photos": [99], "hero": 99}]
    out = arrange._repair(spreads, photos)
    assert sorted(_placed(out)) == [1, 2, 3]
    assert all(s["photos"] for s in out)


def test_unplaced_photos_are_appended():
    photos = [_photo(i) for i in range(1, 6)]
    # Model only placed 1 and 2; 3,4,5 must still make it into the album.
    spreads = [{"photos": [1, 2], "hero": 1}]
    out = arrange._repair(spreads, photos)
    assert sorted(_placed(out)) == [1, 2, 3, 4, 5]


def test_overfull_spread_is_clamped_and_overflow_kept():
    photos = [_photo(i) for i in range(1, 7)]
    spreads = [{"photos": [1, 2, 3, 4, 5, 6], "hero": 1}]
    out = arrange._repair(spreads, photos)
    assert all(len(s["photos"]) <= 4 for s in out)
    # Nothing is lost — the 5th/6th roll into a trailing spread.
    assert sorted(_placed(out)) == [1, 2, 3, 4, 5, 6]


def test_dropped_hero_is_reassigned_to_highest_score_in_spread():
    photos = [_photo(1, 0.2), _photo(2, 0.9), _photo(3, 0.4)]
    # Model named a hero (99) that isn't even on the spread.
    spreads = [{"photos": [1, 2, 3], "hero": 99}]
    out = arrange._repair(spreads, photos)
    assert out[0]["hero"] == 2


def test_hero_in_spread_is_respected():
    photos = [_photo(1, 0.9), _photo(2, 0.2)]
    spreads = [{"photos": [1, 2], "hero": 2}]
    out = arrange._repair(spreads, photos)
    assert out[0]["hero"] == 2
