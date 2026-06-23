"""Look — the vision station: a local vision model looks at each photo.

For every not-yet-analyzed photo, send the image to qwen3-vl on the local fleet
and record (a) a short scene label and (b) a hero_score (0..1) for how album-
cover-worthy the shot is. THIS is the step that makes mnemosyne more than a
ChatGPT wrapper — it reasons about the actual pixels, not a typed description.

Runs entirely on mickey: the image bytes go to localhost Ollama and nowhere else,
so "we never train on / never upload your images" is true by construction.

Phase 3: if config.ARGUS_URL is set, analyze_one delegates to argus service
(using path visible to argus, or local_file upload in future). This provides
the integration adapter path without loading heavy vision models directly in
mnemosyne (keep ARGUS_VISION_BACKEND=mock on argus side).
"""
from __future__ import annotations

import base64
import json
import sqlite3
import time
import urllib.error
import urllib.request

import httpx

from mnemosyne import config

PROMPT = (
    "You are a photo editor culling a restaurant/food photo shoot to design an "
    "album. Look at this single photo and respond ONLY with JSON of the form "
    '{"scene": "...", "hero_score": 0.0}. '
    "scene = a short 3-6 word label of what the shot is and its role in the "
    "story, e.g. 'wide interior establishing shot', 'overhead hero plated dish', "
    "'macro food detail', 'chef plating action', 'cocktail/drink detail', "
    "'closing ambiance shot'. "
    "hero_score = a float 0.0-1.0 for how striking and album-cover-worthy this "
    "ONE image is on its own (a stunning hero dish ~0.9, a small detail ~0.3)."
)


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _generate(payload: dict) -> dict:
    """POST to local Ollama with retries. mickey's Ollama is shared with other
    fleet jobs (Odysseus, the bots' vision lane), so a request can stall or reset
    when another model is mid-inference — back off and retry rather than aborting
    a whole album build over one transient blip."""
    req = urllib.request.Request(
        config.OLLAMA_HOST + "/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Ollama vision call failed after retries: {last}")


def _analyze_one_via_argus(image_path: str) -> dict:
    """Delegate to argus /analyze (path assumed reachable by argus server).
    Maps argus result (shot_type + keywords + culling.hero_potential) to mnemosyne
    {scene, hero_score}. Safe when argus runs with VISION_BACKEND=mock.
    """
    base = (config.ARGUS_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("ARGUS_URL not configured for argus delegation")
    url = f"{base}/analyze"
    try:
        with httpx.Client(timeout=300) as c:
            resp = c.post(url, data={"path": image_path})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Argus delegation failed for {image_path}: {e}") from e

    if "error" in data:
        # graceful fallback shape
        return {"scene": "other", "hero_score": 0.5}

    shot_type = data.get("shot_type", "other")
    keywords = data.get("keywords", []) or []
    scene = f"{shot_type} {keywords[0] if keywords else ''}".strip()[:120]

    culling = data.get("culling", {}) or {}
    try:
        hero_score = float(culling.get("hero_potential", 0.5))
    except (TypeError, ValueError):
        hero_score = 0.5
    hero_score = max(0.0, min(1.0, hero_score))
    return {"scene": scene, "hero_score": round(hero_score, 2)}


def analyze_one(image_path: str) -> dict:
    """Ask the vision model (or delegated argus) about one image; return {scene, hero_score}."""
    if getattr(config, "ARGUS_URL", None):
        return _analyze_one_via_argus(image_path)

    # Original direct Ollama path
    payload = {
        "model": config.VISION_MODEL,
        "prompt": PROMPT,
        "images": [_b64(image_path)],
        "stream": False,
        "format": "json",          # make Ollama emit strict JSON
        "options": {"temperature": 0.2},
    }
    resp = _generate(payload)
    # qwen3-vl is a thinking model: with format=json it emits the answer in the
    # `thinking` field and leaves `response` empty, so fall back to thinking.
    raw = resp.get("response") or resp.get("thinking") or ""
    data = json.loads(raw)

    scene = str(data.get("scene", "")).strip()[:120]
    try:
        score = float(data.get("hero_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return {"scene": scene, "hero_score": max(0.0, min(1.0, score))}


def look_at_album(conn: sqlite3.Connection, album_id: int) -> int:
    """Analyze every not-yet-looked-at photo in the album; return how many were
    analyzed. Idempotent: photos that already have a scene are skipped, so a
    re-run only fills gaps (and a crash mid-way loses no completed work)."""
    rows = conn.execute(
        "SELECT id, path FROM photos WHERE album_id = ? AND scene IS NULL ORDER BY id",
        (album_id,),
    ).fetchall()
    n = 0
    for row in rows:
        result = analyze_one(row["path"])
        conn.execute(
            "UPDATE photos SET scene = ?, hero_score = ? WHERE id = ?",
            (result["scene"], result["hero_score"], row["id"]),
        )
        conn.commit()
        n += 1
    return n
