"""Evaluate — the beat-the-baseline harness (the headline of layout-quality work).

Given a gallery's eligible photos + Mise signals, this produces BOTH the Mise-style
baseline layout and the Mnemosyne layout, proves each is a VALID proposal (it runs
them through proposal.validate_proposal — the same authority Mise uses), scores them
on objective metrics, and emits a side-by-side a human can review and accept. That
comparison is what lets Mise decide to adopt Mnemosyne over the baseline.

Everything here is deterministic and model-free, so a comparison is reproducible
given the same input — the precondition for trusting the numbers.
"""
from __future__ import annotations

from mnemosyne import baseline, curate, proposal
from mnemosyne.signals import hero_of


# --- proposal shaping --------------------------------------------------------


def _placements(spreads: list[dict]) -> list[dict]:
    """Flatten spreads into 0-based, densified placements (spread index, slot index
    by position) — the strict contract shape."""
    out: list[dict] = []
    for spread_idx, spread in enumerate(spreads):
        for slot_idx, pid in enumerate(spread["photos"]):
            out.append({"asset_id": pid, "spread": spread_idx, "slot": slot_idx})
    return out


def proposal_of(
    spreads: list[dict], *, provider: str, model: str, notes: str | None = None
) -> dict:
    """A spreads list as the strict proposal JSON."""
    out: dict = {"placements": _placements(spreads), "provider": provider, "model": model}
    if notes:
        out["notes"] = notes
    return out


# --- scoring -----------------------------------------------------------------


def _variance(values: list[int]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def scorecard(spreads: list[dict], photos: list[dict], *, omitted: list | None = None) -> dict:
    """Objective metrics for one layout — coverage, hero usage, spread balance,
    pacing, opener/closer strength. Numbers only; the human reads them next to the
    rendered album to score quality."""
    by_id = {p["id"]: p for p in photos}
    placed = [pid for s in spreads for pid in s["photos"]]
    sizes = [len(s["photos"]) for s in spreads]
    spread_heroes = [hero_of(by_id[s["hero"]]) for s in spreads if s["hero"] in by_id]
    eligible = len(photos)
    omitted = omitted or []
    best = max(photos, key=lambda p: (hero_of(p), -p["id"])) if photos else None
    return {
        "eligible": eligible,
        "placed": len(placed),
        "omitted": len(omitted),
        "coverage_pct": round(100 * len(placed) / eligible, 1) if eligible else 0.0,
        "spreads": len(spreads),
        "spread_sizes": sizes,
        "size_variance": round(_variance(sizes), 3),
        "solo_hero_spreads": sum(1 for s in sizes if s == 1),
        "mean_spread_hero": round(sum(spread_heroes) / len(spread_heroes), 3)
        if spread_heroes
        else 0.0,
        "cover_hero": round(hero_of(by_id[spreads[0]["hero"]]), 3) if spreads else 0.0,
        "closer_hero": round(hero_of(by_id[spreads[-1]["hero"]]), 3) if spreads else 0.0,
        "best_hero_is_cover": bool(best and spreads and spreads[0]["hero"] == best["id"]),
    }


def _rationale(spread_idx: int, is_hero: bool, size: int) -> str:
    if spread_idx == 0 and is_hero:
        return "cover hero (highest hero_potential)"
    if size == 1:
        return "solo hero spread (room to breathe)"
    if is_hero:
        return "spread hero"
    return "supporting shot"


def acceptance_checklist(spreads: list[dict], photos: list[dict]) -> list[dict]:
    """One reviewable row per placement, each with a rationale and an `accept` slot
    the human fills (True/False). compute_acceptance turns the filled list into the
    headline ≥70%-acceptable number."""
    by_id = {p["id"]: p for p in photos}
    rows: list[dict] = []
    for spread_idx, spread in enumerate(spreads):
        size = len(spread["photos"])
        for slot_idx, pid in enumerate(spread["photos"]):
            is_hero = pid == spread["hero"]
            rows.append(
                {
                    "spread": spread_idx,
                    "slot": slot_idx,
                    "asset_id": pid,
                    "is_hero": is_hero,
                    "hero_potential": round(hero_of(by_id.get(pid, {})), 2),
                    "rationale": _rationale(spread_idx, is_hero, size),
                    "accept": None,
                }
            )
    return rows


def compute_acceptance(checklist: list[dict]) -> dict:
    """Roll a human-filled checklist into the acceptance headline. Targets the album
    bar: ≥~70% of placements acceptable as-is. Returns passes=None until reviewed."""
    answered = [r for r in checklist if r.get("accept") is not None]
    if not answered:
        return {"answered": 0, "accepted": 0, "acceptance_pct": None, "passes": None}
    accepted = sum(1 for r in answered if r["accept"])
    pct = round(100 * accepted / len(answered), 1)
    return {
        "answered": len(answered),
        "accepted": accepted,
        "acceptance_pct": pct,
        "passes": pct >= 70.0,
    }


# --- comparison --------------------------------------------------------------


def compare(
    photos: list[dict],
    *,
    theme: str | None = "food",
    baseline_per_spread: int = 4,
    keeper_floor: float = 0.0,
    target_count: int | None = None,
    solo_hero_floor: float = 0.85,
    anchor_hero_floor: float = 0.7,
    anchor_max: int = 2,
    max_per_spread: int = 4,
) -> dict:
    """Build the baseline and the Mnemosyne layout for one gallery, validate both,
    score both, and bundle a human-review checklist. The returned dict is the full
    comparison artifact (also renderable as Markdown). `theme` drives the narrative
    arc Mnemosyne sequences along."""
    base_spreads = baseline.baseline_layout(photos, per_spread=baseline_per_spread)
    layout = curate.mnemosyne_layout(
        photos,
        theme=theme,
        keeper_floor=keeper_floor,
        target_count=target_count,
        solo_hero_floor=solo_hero_floor,
        anchor_hero_floor=anchor_hero_floor,
        anchor_max=anchor_max,
        max_per_spread=max_per_spread,
    )
    mnem_spreads, omitted = layout["spreads"], layout["omitted"]
    eligible = {p["id"] for p in photos}

    note = f"culled {len(omitted)} of {len(photos)} eligible photos" if omitted else None
    base_prop = proposal_of(base_spreads, provider="mise-baseline", model="deterministic")
    mnem_prop = proposal_of(
        mnem_spreads, provider="mnemosyne", model="deterministic-v1", notes=note
    )

    base_sc = scorecard(base_spreads, photos)
    mnem_sc = scorecard(mnem_spreads, photos, omitted=omitted)
    base_sc["valid"] = proposal.validate_proposal(base_prop, eligible) == []
    mnem_sc["valid"] = proposal.validate_proposal(mnem_prop, eligible) == []

    delta_keys = (
        "size_variance",
        "mean_spread_hero",
        "cover_hero",
        "solo_hero_spreads",
        "omitted",
    )
    deltas = {k: round(mnem_sc[k] - base_sc[k], 3) for k in delta_keys}

    return {
        "baseline": {"proposal": base_prop, "spreads": base_spreads, "scorecard": base_sc},
        "mnemosyne": {
            "proposal": mnem_prop,
            "spreads": mnem_spreads,
            "scorecard": mnem_sc,
            "omitted": omitted,
        },
        "deltas": deltas,
        "checklist": acceptance_checklist(mnem_spreads, photos),
    }


def render_markdown(comparison: dict, *, title: str = "Album comparison") -> str:
    """A human-skimmable side-by-side of the two scorecards, the surfaced cull, and a
    checklist preview. This is the artifact a reviewer reads to score Mnemosyne vs the
    baseline."""
    base = comparison["baseline"]["scorecard"]
    mnem = comparison["mnemosyne"]["scorecard"]
    omitted = comparison["mnemosyne"]["omitted"]
    rows = [
        f"# {title}",
        "",
        "| metric | baseline | mnemosyne |",
        "| --- | --- | --- |",
    ]
    for key in (
        "valid",
        "eligible",
        "placed",
        "omitted",
        "coverage_pct",
        "spreads",
        "size_variance",
        "solo_hero_spreads",
        "mean_spread_hero",
        "cover_hero",
        "closer_hero",
        "best_hero_is_cover",
    ):
        rows.append(f"| {key} | {base.get(key)} | {mnem.get(key)} |")

    rows += ["", "## Surfaced cull (never silent)"]
    if omitted:
        rows += [f"- asset {o['id']}: {o['reason']}" for o in omitted]
    else:
        rows.append("- (none — every eligible photo placed)")

    rows += ["", "## Mnemosyne placements (review checklist)",
             "", "| spread | slot | asset | hero | rationale |",
             "| --- | --- | --- | --- | --- |"]
    for r in comparison["checklist"]:
        mark = " ⭐" if r["is_hero"] else ""
        rows.append(
            f"| {r['spread']} | {r['slot']} | {r['asset_id']}{mark} | "
            f"{r['hero_potential']} | {r['rationale']} |"
        )
    return "\n".join(rows)


# --- fixtures (deterministic, no DB, no network) -----------------------------


def _photo(pid: int, hero: float, keeper: float, scene: str, portrait: bool = False) -> dict:
    w, h = (800, 1200) if portrait else (1200, 800)
    return {
        "id": pid,
        "hero_potential": hero,
        "keeper_score": keeper,
        "scene": scene,
        "width": w,
        "height": h,
    }


def fixture_galleries() -> dict[str, list[dict]]:
    """Representative synthetic galleries with known signal distributions — a few
    keepers and a clear standout, plus weak shots to cull. Used by the CLI demo and
    the tests so the harness runs fully offline and deterministically."""
    wedding = [
        _photo(1, 0.55, 0.7, "getting ready detail", portrait=True),
        _photo(2, 0.20, 0.25, "blurry candid"),
        _photo(3, 0.92, 0.95, "wide ceremony establishing shot"),
        _photo(4, 0.40, 0.5, "ring detail", portrait=True),
        _photo(5, 0.88, 0.9, "couple portrait", portrait=True),
        _photo(6, 0.15, 0.18, "throwaway test frame"),
        _photo(7, 0.60, 0.65, "first dance"),
        _photo(8, 0.78, 0.8, "reception candids"),
        _photo(9, 0.83, 0.86, "send-off moment"),
    ]
    food = [
        _photo(10, 0.30, 0.4, "wide interior establishing shot"),
        _photo(11, 0.95, 0.97, "overhead hero plated dish"),
        _photo(12, 0.50, 0.55, "macro food detail", portrait=True),
        _photo(13, 0.12, 0.15, "out of focus"),
        _photo(14, 0.70, 0.72, "chef plating action"),
        _photo(15, 0.45, 0.48, "cocktail detail", portrait=True),
    ]
    return {"wedding": wedding, "food": food}
