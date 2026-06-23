"""Arrange — the station that turns analyzed photos into a laid-out album.

Takes the photos (with their vision scene labels + hero scores) and decides the
story order, groups them into two-page spreads, and names one hero per spread.
A local reasoning model does the creative call; a deterministic fallback covers
the case where the model returns unusable JSON, so the show station always has a
complete album to render (every photo placed exactly once).
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

from mnemosyne import config

_SYSTEM = (
    "You are an album designer laying out a photo album from a restaurant photo "
    "shoot. You will get a list of photos as (id, scene, hero_score, orientation). "
    "Design the album as an ordered list of two-page SPREADS that tells the story "
    "of the shoot: arrival/exterior -> ambiance -> details & drinks -> hero dishes "
    "-> action -> dessert -> closing. Rules: each spread holds 1 to 4 photos; do "
    "NOT crowd two high hero_score photos onto one spread — give a striking shot "
    "its own spread or make it the clear hero; mix orientations so a spread looks "
    "balanced; use EVERY photo exactly once. "
    'Respond ONLY as JSON: {"spreads": [{"photos": [id, ...], "hero": id}, ...]} '
    "in album order."
)


def _orientation(p: dict) -> str:
    return "portrait" if (p["height"] or 0) > (p["width"] or 0) else "landscape"


def _ask_model(photos: list[dict]) -> list[dict] | None:
    """Ask the reasoning model for a layout. Returns the spread list, or None if
    the response is malformed or doesn't place exactly the photos we have."""
    listing = "\n".join(
        f'- id={p["id"]} scene="{p["scene"]}" hero_score={p["hero_score"]} '
        f"orientation={_orientation(p)}"
        for p in photos
    )
    payload = {
        "model": config.ARRANGE_MODEL,
        "prompt": f"{_SYSTEM}\n\nPhotos:\n{listing}",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.4},
    }
    req = urllib.request.Request(
        config.OLLAMA_HOST + "/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.load(r)
        # qwen3.6 is a thinking model: with format=json the answer lands in the
        # `thinking` field and `response` is empty, so fall back to thinking.
        raw = resp.get("response") or resp.get("thinking") or ""
        spreads = json.loads(raw).get("spreads", [])
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError):
        return None

    # Validate: every photo placed exactly once, hero is on its spread.
    want = {p["id"] for p in photos}
    seen: list[int] = []
    for s in spreads:
        ids = s.get("photos", [])
        if not (1 <= len(ids) <= 4) or s.get("hero") not in ids:
            return None
        seen.extend(ids)
    if sorted(seen) != sorted(want):
        return None
    return spreads


def _fallback(photos: list[dict], per_spread: int = 3) -> list[dict]:
    """Deterministic layout: keep ingest order, chunk into spreads, hero = the
    highest-scoring photo in each chunk. Guarantees a complete, sane album."""
    spreads = []
    for i in range(0, len(photos), per_spread):
        chunk = photos[i : i + per_spread]
        hero = max(chunk, key=lambda p: p["hero_score"] or 0.0)["id"]
        spreads.append({"photos": [p["id"] for p in chunk], "hero": hero})
    return spreads


def arrange_album(conn: sqlite3.Connection, album_id: int) -> int:
    """Lay out the album; return the number of spreads. Replaces any prior layout
    for this album so re-running is safe."""
    photos = [
        dict(r)
        for r in conn.execute(
            "SELECT id, scene, hero_score, width, height FROM photos "
            "WHERE album_id = ? ORDER BY id",
            (album_id,),
        ).fetchall()
    ]
    if not photos:
        return 0

    layout = _ask_model(photos)
    if layout is None:
        import sys

        print(
            "arrange: reasoning model returned no usable layout — using "
            "deterministic fallback (album will be in ingest order, not story order)",
            file=sys.stderr,
        )
        layout = _fallback(photos)

    # Clear any existing layout first (idempotent re-runs).
    conn.execute(
        "DELETE FROM placements WHERE spread_id IN "
        "(SELECT id FROM spreads WHERE album_id = ?)",
        (album_id,),
    )
    conn.execute("DELETE FROM spreads WHERE album_id = ?", (album_id,))

    for pos, spread in enumerate(layout, start=1):
        cur = conn.execute(
            "INSERT INTO spreads (album_id, position, hero_photo_id) VALUES (?, ?, ?)",
            (album_id, pos, spread["hero"]),
        )
        spread_id = cur.lastrowid
        for slot, photo_id in enumerate(spread["photos"], start=1):
            conn.execute(
                "INSERT INTO placements (spread_id, photo_id, slot) VALUES (?, ?, ?)",
                (spread_id, photo_id, slot),
            )
    conn.commit()
    return len(layout)
