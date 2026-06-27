"""Signal accessors — read Mise's per-photo scores off a plain photo dict.

The layout-quality code (baseline, curate, evaluate) works on plain dicts rather
than DB rows so it stays pure, model-free, and trivially testable. These helpers are
the one place that knows the score field names, so a photo can arrive as Mise's
shape (`hero_potential`, `keeper_score`), Mnemosyne's stored shape (`hero_score`), or
the already-normalized `hero`/`keeper` — all read the same. Everything is clamped to
0..1 and given an honest default, so a missing or junk score can never crash a sort
or push a layout out of range.
"""
from __future__ import annotations

from typing import Any


def _clamp01(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def hero_of(photo: dict, default: float = 0.0) -> float:
    """How album-cover-worthy this photo is (0..1). Mise's `hero_potential`, else
    Mnemosyne's stored `hero_score`, else the normalized `hero`; default 0.0."""
    for key in ("hero", "hero_potential", "hero_score"):
        if photo.get(key) is not None:
            return _clamp01(photo[key], default)
    return default


def keeper_of(photo: dict) -> float:
    """How strong a keeper this photo is (0..1) — the cull signal. Mise's
    `keeper_score`, else the normalized `keeper`. Absent → falls back to hero, so a
    gallery without keeper scores ranks by hero rather than culling blindly."""
    for key in ("keeper", "keeper_score"):
        if photo.get(key) is not None:
            return _clamp01(photo[key], hero_of(photo))
    return hero_of(photo)
