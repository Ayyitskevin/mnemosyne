"""Layout — turn an arranged spread (its photos + which one is the hero) into
real album geometry: pick a spread template by photo count and orientation, and
hand each photo a CSS grid area so portraits land in tall slots and landscapes in
wide ones, with the hero given the dominant space.

Deterministic by design (Rule 5): the *creative* calls — which photos belong on a
spread and which is the hero — were already made upstream by the vision/arrange
stations. This station only decides geometry, and geometry is a pure function of
photo count + orientations, so it's plain code, not another model call.

A spread is a two-page unit drawn as one CSS grid. The hero always takes grid
area "a"; the remaining photos fill "b", "c", "d" in slot order.
"""
from __future__ import annotations


def orientation(photo: dict) -> str:
    """'P' for a taller-than-wide photo, 'L' otherwise."""
    return "P" if (photo.get("height") or 0) > (photo.get("width") or 0) else "L"


def _template(n: int, hero_orient: str, orients: list[str]) -> dict:
    """The grid frame for a spread: column/row tracks, the grid-template-areas
    string, and the ordered area names the non-hero photos fill.

    Templates are chosen so a photo's slot shape matches its orientation: a single
    striking shot goes full-bleed; two portraits sit side by side while two
    landscapes stack; a landscape hero spans wide across the top while a portrait
    hero runs tall down one page.
    """
    if n == 1:
        return {"cols": "1fr", "rows": "1fr", "areas": '"a"', "fill": []}

    if n == 2:
        if orients == ["P", "P"]:
            return {"cols": "1fr 1fr", "rows": "1fr", "areas": '"a b"', "fill": ["b"]}
        if orients == ["L", "L"]:
            return {"cols": "1fr", "rows": "1fr 1fr", "areas": '"a" "b"', "fill": ["b"]}
        # Mixed orientations: side by side, hero gets the wider page.
        return {"cols": "1.25fr 1fr", "rows": "1fr", "areas": '"a b"', "fill": ["b"]}

    if n == 3:
        if hero_orient == "L":
            # Wide hero across the top, two supporting shots beneath it.
            return {"cols": "1fr 1fr", "rows": "1.4fr 1fr",
                    "areas": '"a a" "b c"', "fill": ["b", "c"]}
        # Tall hero down the left page, two stacked on the right.
        return {"cols": "1.3fr 1fr", "rows": "1fr 1fr",
                "areas": '"a b" "a c"', "fill": ["b", "c"]}

    # n == 4
    if hero_orient == "L":
        return {"cols": "1fr 1fr 1fr", "rows": "1.5fr 1fr",
                "areas": '"a a a" "b c d"', "fill": ["b", "c", "d"]}
    return {"cols": "1.2fr 1fr", "rows": "1fr 1fr 1fr",
            "areas": '"a b" "a c" "a d"', "fill": ["b", "c", "d"]}


def plan_spread(photos: list[dict], hero_photo_id: int | None) -> dict:
    """Geometry for one spread. `photos` are in slot order; returns the grid track
    definitions plus a `slots` list where each photo carries its grid `area` and
    an `is_hero` flag. Every photo is placed exactly once.

    Spreads carry 1-4 photos (the arrange station guarantees this); anything past
    4 is clamped into the 4-photo template so the renderer never breaks.
    """
    if not photos:
        return {"cols": "1fr", "rows": "1fr", "areas": '"a"', "slots": []}

    hero = next((p for p in photos if p["id"] == hero_photo_id), photos[0])
    others = [p for p in photos if p["id"] != hero["id"]]
    orients = [orientation(p) for p in photos]
    n = min(len(photos), 4)
    tpl = _template(n, orientation(hero), orients)

    slots = [{"photo": hero, "area": "a", "is_hero": True}]
    for photo, area in zip(others, tpl["fill"]):
        slots.append({"photo": photo, "area": area, "is_hero": False})
    return {"cols": tpl["cols"], "rows": tpl["rows"], "areas": tpl["areas"], "slots": slots}
