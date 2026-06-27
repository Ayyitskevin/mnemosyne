"""Arrange — the station that turns analyzed photos into a laid-out album.

Takes the photos (with their vision scene labels + hero scores) and decides the
story order, groups them into two-page spreads, and names one hero per spread.
A local reasoning model does the creative call; a deterministic fallback covers
the case where the model returns unusable JSON, so the show station always has a
complete album to render (every photo placed exactly once).

There is also a fully deterministic LAYOUT ENGINE (curate.py): set
MNEMOSYNE_ARRANGE_BACKEND=deterministic to lay out the album with no model at all —
arc-sequenced, hero-covered, variably paced from the stored hero/keeper signals.
It's opt-in so the existing model path is the default, and its output is checked to
place every photo exactly once before it's committed (Rule: the engine proposes,
code still verifies).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import httpx2 as httpx

from mnemosyne import config, curate, usage
from mnemosyne.themes import arrange_system


def _orientation(p: dict) -> str:
    return "portrait" if (p["height"] or 0) > (p["width"] or 0) else "landscape"


def _listing(photos: list[dict]) -> str:
    return "\n".join(
        f'- id={p["id"]} scene="{p["scene"]}" hero_score={p["hero_score"]} '
        f"orientation={_orientation(p)}"
        for p in photos
    )


def _spreads_or_none(spreads, photos: list[dict]) -> list[dict] | None:
    """Validate + repair a model's raw spread list into a complete layout, or None
    when the model gave nothing usable so the caller falls back deterministically."""
    if not isinstance(spreads, list) or not spreads:
        return None
    return _repair(spreads, photos)


def _ask_model(
    photos: list[dict], *, theme: str
) -> tuple[list[dict] | None, dict | None]:
    """Ask the reasoning model for a layout. Returns (layout, usage_meta): the
    repaired spread list (or None if unusable, so the caller falls back), plus a
    billed-call usage block for the cloud backend (None for the free local one).
    Backend is OPT-IN like vision — grok only when MNEMOSYNE_ARRANGE_BACKEND=grok,
    so the local dogfood path is never silently swapped for a billed call."""
    system = arrange_system(theme)
    if (config.ARRANGE_BACKEND or "").strip().lower() == "grok":
        return _arrange_via_grok(photos, system=system)
    return _arrange_via_ollama(photos, system=system)


def _arrange_via_ollama(
    photos: list[dict], *, system: str
) -> tuple[list[dict] | None, None]:
    """Local reasoning model over localhost Ollama — free, unmetered (usage None)."""
    payload = {
        "model": config.ARRANGE_MODEL,
        "prompt": f"{system}\n\nPhotos:\n{_listing(photos)}",
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
        return None, None
    return _spreads_or_none(spreads, photos), None


def _arrange_via_grok(
    photos: list[dict], *, system: str
) -> tuple[list[dict] | None, dict | None]:
    """Cloud reasoning over xAI's chat API — the lane a real (Ollama-less) host
    runs on. Returns (layout, usage_meta). Follows arrange's always-ship-an-album
    contract: any problem (missing key, HTTP error, bad JSON) returns a None layout
    so the deterministic fallback still produces an album — but it's logged LOUD to
    stderr, never a silent degrade (R12/R14). A successful call returns its token
    usage so arrange_album can meter the COGS."""
    if not config.XAI_API_KEY:
        print("arrange: ARRANGE_BACKEND=grok but XAI_API_KEY is unset — falling back "
              "to deterministic layout", file=sys.stderr)
        return None, None
    payload = {
        "model": config.GROK_ARRANGE_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Photos:\n{_listing(photos)}"},
        ],
        "temperature": 0.4,
    }
    url = config.XAI_BASE_URL.rstrip("/") + "/chat/completions"
    started = time.monotonic()
    try:
        with httpx.Client(timeout=300) as c:
            resp = c.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
            )
            resp.raise_for_status()
            body = resp.json()
        latency = time.monotonic() - started
        content = body["choices"][0]["message"]["content"]
        # Chat models wrap JSON in prose/fences, so slice to the outermost braces
        # before parsing (same robustness the vision parse leans on).
        start, end = content.find("{"), content.rfind("}")
        spreads = json.loads(content[start : end + 1]).get("spreads", [])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        print(f"arrange: grok layout call failed ({e}) — falling back to "
              "deterministic layout", file=sys.stderr)
        return None, None

    meta = {
        "backend": "grok",
        "model": config.GROK_ARRANGE_MODEL,
        "tokens": body.get("usage", {}) or {},
        "latency": latency,
    }
    return _spreads_or_none(spreads, photos), meta


def _repair(spreads: list[dict], photos: list[dict]) -> list[dict]:
    """Reconcile the model's spreads against the real photo set. The reasoning
    model reliably nails the story order and grouping but routinely fumbles the
    exact ids — duplicating one, hallucinating a non-existent one. That's a
    deterministic correctness problem, not a creative one (Rule 5), so we fix it
    in code instead of rejecting good sequencing: drop unknown/duplicate ids,
    clamp spreads to 4, then pack any photo the model never placed into trailing
    spreads. Result is valid by construction — every real photo placed once."""
    valid = {p["id"]: p for p in photos}

    def _hero(ids: list[int], wanted: int | None) -> int:
        if wanted in ids:
            return wanted
        return max(ids, key=lambda i: valid[i]["hero_score"] or 0.0)

    placed: set[int] = set()
    repaired: list[dict] = []
    for s in spreads:
        ids: list[int] = []
        for pid in s.get("photos", []):
            if len(ids) >= 4:
                break
            if pid in valid and pid not in placed:
                ids.append(pid)
                placed.add(pid)
        if ids:
            repaired.append({"photos": ids, "hero": _hero(ids, s.get("hero"))})

    missing = [p["id"] for p in photos if p["id"] not in placed]
    for i in range(0, len(missing), 4):
        chunk = missing[i : i + 4]
        repaired.append({"photos": chunk, "hero": _hero(chunk, None)})

    return repaired


def _fallback(photos: list[dict], per_spread: int = 3) -> list[dict]:
    """Deterministic layout: keep ingest order, chunk into spreads, hero = the
    highest-scoring photo in each chunk. Guarantees a complete, sane album."""
    spreads = []
    for i in range(0, len(photos), per_spread):
        chunk = photos[i : i + per_spread]
        hero = max(chunk, key=lambda p: p["hero_score"] or 0.0)["id"]
        spreads.append({"photos": [p["id"] for p in chunk], "hero": hero})
    return spreads


def _engine_layout(photos: list[dict], *, theme: str) -> list[dict]:
    """Lay the album out with the deterministic curate engine — arc-sequenced,
    hero-covered, variably paced from the stored hero/keeper signals. Places every
    photo once (no cull in the live path: keeper_floor 0), returning the same
    spreads shape (`{"photos": [...], "hero": id}`) the model/repair path produces."""
    return curate.mnemosyne_layout(photos, theme=theme)["spreads"]


def _places_every_photo_once(layout: list[dict], photos: list[dict]) -> bool:
    placed = [pid for s in layout for pid in s["photos"]]
    return sorted(placed) == sorted(p["id"] for p in photos)


def arrange_album(conn: sqlite3.Connection, album_id: int) -> int:
    """Lay out the album; return the number of spreads. Replaces any prior layout
    for this album so re-running is safe."""
    photos = [
        dict(r)
        for r in conn.execute(
            "SELECT id, scene, hero_score, keeper_score, width, height FROM photos "
            "WHERE album_id = ? ORDER BY id",
            (album_id,),
        ).fetchall()
    ]
    if not photos:
        return 0

    theme_row = conn.execute(
        "SELECT gallery_theme FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    theme = (theme_row["gallery_theme"] if theme_row else None) or "food"

    if (config.ARRANGE_BACKEND or "").strip().lower() == "deterministic":
        # No model: the engine lays it out from the signals, reproducibly.
        layout, usage_meta = _engine_layout(photos, theme=theme), None
    else:
        layout, usage_meta = _ask_model(photos, theme=theme)

    # Verify at the source — never commit a layout that drops or duplicates a photo,
    # whichever path produced it. A bad/empty one falls back to the safe chunker.
    if layout is None or not _places_every_photo_once(layout, photos):
        if layout is not None:
            print(
                "arrange: layout did not place every photo exactly once — using "
                "deterministic fallback",
                file=sys.stderr,
            )
        else:
            print(
                "arrange: reasoning model returned no usable layout — using "
                "deterministic fallback (album will be in ingest order, not story order)",
                file=sys.stderr,
            )
        layout = _fallback(photos)

    # Meter the call if it was a billed cloud one (local Ollama returns no usage).
    if usage_meta:
        usage.record(conn, album_id=album_id, photo_id=None, stage="arrange", **usage_meta)

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
