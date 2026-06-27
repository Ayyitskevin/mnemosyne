"""Curate — the deterministic, signal-driven core of a Mnemosyne layout.

This is the "intelligence" the baseline lacks, expressed as pure functions over
Mise's signals so it is fully reproducible (no model, no randomness):

  select_and_sequence  — cull weak shots by keeper_score (omissions SURFACED, never
                         silently dropped), then order the keepers along the shoot's
                         narrative arc with the best hero as a cover and a strong closer.
  group_into_spreads    — pace the ordered keepers into spreads of VARYING size: a
                         standout hero gets its own spread, a strong shot anchors a
                         smaller one, the rest pack up to a cap.
  mnemosyne_layout      — the two composed: photos -> {"spreads", "omitted"}.

Correctness invariant (the guardrail Mise enforces): the selected set and the
omitted set partition the eligible photos exactly — every eligible photo is either
placed once or surfaced as omitted, never both and never neither.

Sequencing follows the theme's story arc (arc.py) from the `scene` labels, with the
single best hero pulled to the cover and the strongest remaining hero held for the
close; unlabelled shots fall back to capture order. Everything stays deterministic.
"""
from __future__ import annotations

from mnemosyne import arc
from mnemosyne.signals import hero_of, keeper_of


def select_and_sequence(
    photos: list[dict],
    *,
    theme: str | None = "food",
    keeper_floor: float = 0.0,
    target_count: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pick the keepers and order them. Returns (selected, omitted):

      * selected — the kept photos, ordered along the theme's narrative arc with the
        best hero as a cover and the strongest remaining hero held for the close.
      * omitted  — [{"id", "reason"}] for every dropped photo, so a cull is always
        visible to Mise, never silent.

    Cull rules: drop photos whose keeper_score is below `keeper_floor`; if more than
    `target_count` survive, keep the strongest `target_count` (by keeper, then hero)
    and surface the rest. A non-empty gallery is NEVER culled to empty — if every
    photo is below the floor, the single strongest is kept so there is always an
    album to review.
    """
    if keeper_floor < 0:
        raise ValueError("keeper_floor must be >= 0")
    # Strongest first, with id as a stable final tiebreak for reproducibility.
    ranked = sorted(photos, key=lambda p: (-keeper_of(p), -hero_of(p), p["id"]))

    kept = [p for p in ranked if keeper_of(p) >= keeper_floor]
    omitted = [
        {"id": p["id"], "reason": f"keeper {keeper_of(p):.2f} below floor {keeper_floor:.2f}"}
        for p in ranked
        if keeper_of(p) < keeper_floor
    ]
    if not kept and ranked:
        # Never cull a real gallery to nothing — keep the single best shot.
        best = ranked[0]
        kept = [best]
        omitted = [o for o in omitted if o["id"] != best["id"]]

    if target_count is not None and len(kept) > target_count:
        if target_count < 1:
            raise ValueError("target_count must be >= 1")
        over = kept[target_count:]
        kept = kept[:target_count]
        omitted.extend(
            {"id": p["id"], "reason": f"beyond target album size {target_count} (lowest keepers)"}
            for p in over
        )

    return _sequence(kept, theme), omitted


def _sequence(kept: list[dict], theme: str | None) -> list[dict]:
    """Order keepers as cover + arc-ordered middle + closer. The cover is the single
    best hero; the closer is the best hero of what's left; the middle follows the
    theme's narrative arc (arc.bucket_index), capture order breaking ties so an
    unlabelled run stays chronological. Every kept photo appears exactly once."""
    if len(kept) <= 1:
        return list(kept)
    cover = max(kept, key=lambda p: (hero_of(p), -p["id"]))
    rest = [p for p in kept if p["id"] != cover["id"]]
    if len(rest) == 1:
        return [cover, rest[0]]
    closer = max(rest, key=lambda p: (hero_of(p), -p["id"]))
    middle = sorted(
        (p for p in rest if p["id"] != closer["id"]),
        key=lambda p: (arc.bucket_index(theme, p.get("scene")), p["id"]),
    )
    return [cover, *middle, closer]


def group_into_spreads(
    selected: list[dict],
    *,
    solo_hero_floor: float = 0.85,
    anchor_hero_floor: float = 0.7,
    anchor_max: int = 2,
    max_per_spread: int = 4,
) -> list[dict]:
    """Pace the ordered keepers into spreads of varying size so the album has rhythm
    instead of a flat N. Three tiers give heroes room:

      * a standout hero (>= solo_hero_floor) gets its OWN full-bleed spread;
      * a strong hero (>= anchor_hero_floor) leads a smaller spread capped at
        `anchor_max`, so it shares a page with at most a shot or two;
      * everything else packs up to `max_per_spread`.

    Each spread's hero is its strongest photo. Order is preserved and every selected
    photo is placed exactly once."""
    if min(anchor_max, max_per_spread) < 1:
        raise ValueError("anchor_max and max_per_spread must be >= 1")
    spreads: list[dict] = []
    buf: list[dict] = []
    cap = max_per_spread

    def emit(photos: list[dict]) -> None:
        hero = max(photos, key=lambda p: (hero_of(p), -p["id"]))["id"]
        spreads.append({"photos": [p["id"] for p in photos], "hero": hero})

    for photo in selected:
        hero = hero_of(photo)
        if hero >= solo_hero_floor:
            if buf:
                emit(buf)
                buf, cap = [], max_per_spread
            emit([photo])
            continue
        if hero >= anchor_hero_floor and buf:
            emit(buf)  # an anchor leads a fresh spread, so close the current one
            buf, cap = [], max_per_spread
        buf.append(photo)
        if hero >= anchor_hero_floor and len(buf) == 1:
            cap = anchor_max  # this spread is led by an anchor → keep it small
        if len(buf) >= cap:
            emit(buf)
            buf, cap = [], max_per_spread
    if buf:
        emit(buf)
    return spreads


def mnemosyne_layout(
    photos: list[dict],
    *,
    theme: str | None = "food",
    keeper_floor: float = 0.0,
    target_count: int | None = None,
    solo_hero_floor: float = 0.85,
    anchor_hero_floor: float = 0.7,
    anchor_max: int = 2,
    max_per_spread: int = 4,
) -> dict:
    """The deterministic Mnemosyne layout: cull + arc-sequence + pace. Returns
    {"spreads": [...], "omitted": [...]} — the spreads to render and the surfaced
    cull. Pure and reproducible: same photos + theme + knobs in, same layout out."""
    selected, omitted = select_and_sequence(
        photos, theme=theme, keeper_floor=keeper_floor, target_count=target_count
    )
    spreads = group_into_spreads(
        selected,
        solo_hero_floor=solo_hero_floor,
        anchor_hero_floor=anchor_hero_floor,
        anchor_max=anchor_max,
        max_per_spread=max_per_spread,
    )
    return {"spreads": spreads, "omitted": omitted}
