"""The Mise-style deterministic baseline album proposer — the yardstick to beat.

Mise already ships a baseline: the gallery's eligible photos in id order, chunked
into fixed-size spreads. Mnemosyne is only worth adopting if it beats this. We keep
the baseline deliberately *charitable* — each spread's hero is its strongest shot,
not photo[0] — so a Mnemosyne win is a real win on selection, sequencing, and pacing
rather than a win against a strawman.

A "layout" here is the same intermediate shape arrange uses: an ordered list of
spreads, each `{"photos": [id, ...], "hero": id}`. evaluate turns it into the strict
proposal JSON and validates it.
"""
from __future__ import annotations

from mnemosyne.signals import hero_of


def baseline_layout(photos: list[dict], *, per_spread: int = 4) -> list[dict]:
    """Eligible photos in id order, packed `per_spread` to a spread, hero = the
    highest-hero photo in each chunk. No cull, no sequencing, no pacing — every
    photo is placed exactly once. Deterministic: id order in, stable hero tiebreak
    (lowest id) so the same gallery always yields the same baseline."""
    if per_spread < 1:
        raise ValueError("per_spread must be >= 1")
    ordered = sorted(photos, key=lambda p: p["id"])
    spreads: list[dict] = []
    for i in range(0, len(ordered), per_spread):
        chunk = ordered[i : i + per_spread]
        hero = max(chunk, key=lambda p: (hero_of(p), -p["id"]))["id"]
        spreads.append({"photos": [p["id"] for p in chunk], "hero": hero})
    return spreads
