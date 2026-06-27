"""Curate — the deterministic, signal-driven core of a Mnemosyne layout.

This is the "intelligence" the baseline lacks, expressed as pure functions over
Mise's signals so it is fully reproducible (no model, no randomness):

  select_and_sequence  — cull weak shots by keeper_score (omissions SURFACED, never
                         silently dropped), then order the keepers with a deliberate
                         strong opener and closer.
  group_into_spreads    — pace the ordered keepers into spreads of VARYING size: a
                         standout hero gets its own spread; the rest pack up to a cap.
  mnemosyne_layout      — the two composed: photos -> {"spreads", "omitted"}.

Correctness invariant (the guardrail Mise enforces): the selected set and the
omitted set partition the eligible photos exactly — every eligible photo is either
placed once or surfaced as omitted, never both and never neither.

Narrative-arc sequencing from the `scene` labels is a deliberate follow-up; this
first cut sequences by capture order (id) with hero-driven open/close, which already
beats the baseline's flat id-order-no-cull while staying simple and deterministic.
"""
from __future__ import annotations

from mnemosyne.signals import hero_of, keeper_of


def select_and_sequence(
    photos: list[dict],
    *,
    keeper_floor: float = 0.0,
    target_count: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pick the keepers and order them. Returns (selected, omitted):

      * selected — the kept photos, ordered (a strong opener, a chronological middle,
        a strong closer).
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

    return _sequence(kept), omitted


def _sequence(kept: list[dict]) -> list[dict]:
    """Order keepers as opener + chronological middle + closer. The opener is the
    single best hero (the cover candidate); the closer is the best hero of what's
    left; the middle stays in capture (id) order. Every kept photo appears once."""
    if len(kept) <= 1:
        return list(kept)
    by_id = sorted(kept, key=lambda p: p["id"])
    opener = max(by_id, key=lambda p: (hero_of(p), -p["id"]))
    rest = [p for p in by_id if p["id"] != opener["id"]]
    if not rest:
        return [opener]
    closer = max(rest, key=lambda p: (hero_of(p), -p["id"]))
    middle = [p for p in rest if p["id"] != closer["id"]]
    return [opener, *middle, closer]


def group_into_spreads(
    selected: list[dict], *, solo_hero_floor: float = 0.85, max_per_spread: int = 4
) -> list[dict]:
    """Pace the ordered keepers into spreads of varying size. A standout hero
    (hero >= solo_hero_floor) gets its OWN spread so it has room to breathe; everyone
    else packs up to `max_per_spread`. Each spread's hero is its strongest photo.
    Order is preserved and every selected photo is placed exactly once."""
    if max_per_spread < 1:
        raise ValueError("max_per_spread must be >= 1")
    spreads: list[dict] = []
    buf: list[dict] = []

    def flush() -> None:
        if buf:
            hero = max(buf, key=lambda p: (hero_of(p), -p["id"]))["id"]
            spreads.append({"photos": [p["id"] for p in buf], "hero": hero})
            buf.clear()

    for photo in selected:
        if hero_of(photo) >= solo_hero_floor:
            flush()  # close any pending packed spread before the solo hero
            spreads.append({"photos": [photo["id"]], "hero": photo["id"]})
        else:
            buf.append(photo)
            if len(buf) >= max_per_spread:
                flush()
    flush()
    return spreads


def mnemosyne_layout(
    photos: list[dict],
    *,
    keeper_floor: float = 0.0,
    target_count: int | None = None,
    solo_hero_floor: float = 0.85,
    max_per_spread: int = 4,
) -> dict:
    """The deterministic Mnemosyne layout: cull + sequence + pace. Returns
    {"spreads": [...], "omitted": [...]} — the spreads to render and the surfaced
    cull. Pure and reproducible: same photos + knobs in, same layout out."""
    selected, omitted = select_and_sequence(
        photos, keeper_floor=keeper_floor, target_count=target_count
    )
    spreads = group_into_spreads(
        selected, solo_hero_floor=solo_hero_floor, max_per_spread=max_per_spread
    )
    return {"spreads": spreads, "omitted": omitted}
