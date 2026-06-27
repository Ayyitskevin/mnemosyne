"""Tests for the Mise-style baseline proposer — the yardstick Mnemosyne must beat.

It must be the honest baseline: every photo in id order, fixed N per spread, a real
hero per spread (not a strawman), each photo placed exactly once, deterministic.
"""
from __future__ import annotations

from mnemosyne import baseline


def _photo(pid: int, hero: float = 0.5) -> dict:
    return {"id": pid, "hero_potential": hero, "keeper_score": hero}


def _placed(spreads: list[dict]) -> list[int]:
    return [pid for s in spreads for pid in s["photos"]]


def test_id_order_fixed_chunks():
    photos = [_photo(p) for p in (3, 1, 2, 5, 4)]
    spreads = baseline.baseline_layout(photos, per_spread=2)
    assert [s["photos"] for s in spreads] == [[1, 2], [3, 4], [5]]


def test_every_photo_placed_exactly_once():
    photos = [_photo(p) for p in range(1, 8)]
    placed = _placed(baseline.baseline_layout(photos, per_spread=3))
    assert sorted(placed) == list(range(1, 8))
    assert len(placed) == len(set(placed))


def test_hero_is_strongest_in_chunk():
    photos = [_photo(1, 0.2), _photo(2, 0.9), _photo(3, 0.4)]
    spreads = baseline.baseline_layout(photos, per_spread=3)
    assert spreads[0]["hero"] == 2


def test_deterministic():
    photos = [_photo(p, hero=(p % 3) / 3) for p in range(1, 10)]
    assert baseline.baseline_layout(photos) == baseline.baseline_layout(photos)


def test_empty_gallery():
    assert baseline.baseline_layout([]) == []
