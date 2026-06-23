"""Look — the vision station: a local vision model looks at each photo.

For every not-yet-analyzed photo, send the image to qwen3-vl on the local fleet
and record (a) a short scene label and (b) a hero_score (0..1) for how album-
cover-worthy the shot is. THIS is the step that makes mnemosyne more than a
ChatGPT wrapper — it reasons about the actual pixels, not a typed description.

Default path runs entirely on mickey: the image bytes go to localhost Ollama and
nowhere off the local fleet, so "we never train on your images / never send them to
a third party" holds by construction.

Phase 3: if config.ARGUS_URL is set, analyze_one delegates to the (self-hosted) argus
service — a shared-disk path, or a multipart upload of the bytes to argus when
MNEMOSYNE_ARGUS_DELEGATION_MODE=upload. Either way the image stays inside your own
infrastructure; this provides the integration adapter path without loading heavy
vision models directly in mnemosyne (keep ARGUS_VISION_BACKEND=mock on argus side).
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx2 as httpx
from PIL import Image

from mnemosyne import config, storage, usage

PROMPT = (
    "You are a photo editor culling a restaurant/food photo shoot to design an "
    "album. Look at this single photo and respond ONLY with JSON of the form "
    '{"scene": "...", "hero_score": 0.0}. '
    "scene = a short 3-6 word label of what the shot is and its role in the "
    "story, e.g. 'wide interior establishing shot', 'overhead hero plated dish', "
    "'macro food detail', 'chef plating action', 'cocktail/drink detail', "
    "'closing ambiance shot'. "
    "hero_score = a float 0.0-1.0 for how striking and album-cover-worthy this ONE "
    "image is on its own. BE DISCERNING AND USE THE FULL RANGE — in any real "
    "gallery MOST shots are NOT heroes, so most scores should fall BELOW 0.6. "
    "Reserve high scores; do not bunch everything near 0.8. Calibrate to this "
    "rubric: "
    "0.9-1.0 = exceptional, genuinely cover-worthy (a stunning plated hero dish, "
    "a show-stopping wide); "
    "0.7-0.85 = strong feature shot, would anchor a spread but not the cover; "
    "0.4-0.65 = solid supporting shot (ambiance, table setting, a nice drink); "
    "0.2-0.35 = a small detail or context filler (a macro crumb, a napkin, a "
    "hand); "
    "0.0-0.15 = weak or throwaway. "
    "A pleasant cocktail or latte-art detail is supporting (~0.4-0.5), NOT a hero. "
    "Judge THIS photo honestly against that scale."
)


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _downscale_b64(path: str, max_px: int = 512) -> str:
    """Base64 of the image downscaled so its longest side is <= max_px, as JPEG.

    The biggest cost+privacy lever on the cloud vision path: the vision model only
    needs to judge scene + hero-worthiness, which a 512px thumbnail answers as well
    as a 40MP raw. Downscaling means (a) far fewer image tokens billed per photo and
    (b) the vendor only ever receives a derivative, never the full-res sellable file.
    """
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _parse_scene_hero(raw: str) -> dict:
    """Coerce a model's JSON-ish reply into a clean {scene, hero_score} dict.

    Cloud chat models sometimes wrap JSON in ```json fences or prose, so we slice to
    the outermost braces before parsing. scene is clamped to 120 chars and hero_score
    to 0..1 — the deterministic guardrail that keeps a chatty model from breaking the
    arrange step (CLAUDE.md Rule 5: the model judges, code validates)."""
    raw = (raw or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    scene = str(data.get("scene", "")).strip()[:120]
    try:
        score = float(data.get("hero_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return {"scene": scene, "hero_score": max(0.0, min(1.0, score))}


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


def _argus_auth_headers() -> dict[str, str]:
    token = getattr(config, "ARGUS_API_TOKEN", None)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _argus_delegation_mode() -> str:
    mode = (getattr(config, "ARGUS_DELEGATION_MODE", None) or "path").strip().lower()
    return mode if mode in ("path", "upload") else "path"


def _analyze_one_via_argus(image_path: str) -> dict:
    """Delegate to argus /analyze via shared path or multipart upload.
    Maps argus result (shot_type + keywords + culling.hero_potential) to mnemosyne
    {scene, hero_score}. Safe when argus runs with VISION_BACKEND=mock.
    """
    base = (config.ARGUS_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("ARGUS_URL not configured for argus delegation")
    url = f"{base}/analyze"
    headers = _argus_auth_headers()
    mode = _argus_delegation_mode()
    try:
        with httpx.Client(timeout=300) as c:
            if mode == "upload":
                mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                with open(image_path, "rb") as fh:
                    resp = c.post(
                        url,
                        files={"file": (Path(image_path).name, fh, mime)},
                        headers=headers,
                    )
            else:
                resp = c.post(url, data={"path": image_path}, headers=headers)
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


def _analyze_one_via_ollama(image_path: str) -> dict:
    """Original local path: full-res image to mickey's qwen3-vl over localhost."""
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
    return _parse_scene_hero(raw)


def _analyze_one_via_grok(image_path: str) -> dict:
    """SaaS runtime path: a 512px thumbnail to xAI's OpenAI-compatible vision API.

    Logs token usage + latency to mickey-routing.log so $/album is measurable from
    call one (the per-photo cost is the COGS driver that scales with gallery size).
    We deliberately DON'T send response_format — some xAI vision params 400 — and
    lean on the strong "respond ONLY with JSON" prompt + the robust parse instead.
    """
    if not config.XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY not set — grok vision backend selected but no key")
    b64 = _downscale_b64(image_path)
    payload = {
        "model": config.GROK_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "low",
                        },
                    },
                ],
            }
        ],
        "temperature": 0.2,
    }
    url = config.XAI_BASE_URL.rstrip("/") + "/chat/completions"
    started = time.monotonic()
    try:
        with httpx.Client(timeout=120) as c:
            resp = c.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        raise RuntimeError(f"xAI vision call failed for {image_path}: {e}") from e
    latency = time.monotonic() - started

    tokens = body.get("usage", {}) or {}
    _log_grok_usage(image_path, tokens, latency)

    content = body["choices"][0]["message"]["content"]
    out = _parse_scene_hero(content)
    # Carry the billed-call metering up to look_at_album, which has the conn +
    # album/photo ids to write the inference_usage row. The model function itself
    # stays DB-free — only the local + argus paths omit this key (they're unmetered).
    out["usage_meta"] = {
        "backend": "grok",
        "model": config.GROK_VISION_MODEL,
        "tokens": tokens,
        "latency": latency,
    }
    return out


def _log_grok_usage(image_path: str, usage: dict, latency: float) -> None:
    """One line per billed call so cost-per-album is reconstructable. Best-effort —
    instrumentation must never crash an album build, so logging failures are swallowed."""
    try:
        line = (
            f"{time.strftime('%Y-%m-%dT%H:%M:%S')} · vision · {config.GROK_VISION_MODEL}"
            f" · {config.XAI_BASE_URL} · {latency:.2f}s · "
            f"prompt={usage.get('prompt_tokens', '?')} "
            f"completion={usage.get('completion_tokens', '?')} "
            f"total={usage.get('total_tokens', '?')} · ok\n"
        )
        with open(config.ROUTING_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def analyze_one(image_path: str) -> dict:
    """Ask the selected vision backend about one image; return {scene, hero_score}.

    Backend precedence: explicit config.VISION_BACKEND wins; otherwise local-first
    default (argus if ARGUS_URL is set, else ollama). The grok path is OPT-IN only,
    so the local dogfood/dev path is never silently swapped for a billed cloud call.
    """
    backend = (config.VISION_BACKEND or "").strip().lower()
    if backend == "grok":
        return _analyze_one_via_grok(image_path)
    if backend == "ollama":
        return _analyze_one_via_ollama(image_path)
    if backend == "argus" or (not backend and getattr(config, "ARGUS_URL", None)):
        return _analyze_one_via_argus(image_path)
    return _analyze_one_via_ollama(image_path)


def look_at_album(conn: sqlite3.Connection, album_id: int) -> int:
    """Analyze every not-yet-looked-at photo in the album; return how many were
    analyzed. Idempotent: photos that already have a scene are skipped, so a
    re-run only fills gaps (and a crash mid-way loses no completed work)."""
    rows = conn.execute(
        "SELECT id, storage_key FROM photos "
        "WHERE album_id = ? AND scene IS NULL ORDER BY id",
        (album_id,),
    ).fetchall()
    store = storage.get_storage()
    n = 0
    for row in rows:
        # Resolve the storage key to a real path for the life of the analyze call;
        # a remote driver downloads + cleans up around this block, the local one
        # just hands back the file.
        with store.open_path(row["storage_key"]) as image_path:
            result = analyze_one(str(image_path))
        conn.execute(
            "UPDATE photos SET scene = ?, hero_score = ? WHERE id = ?",
            (result["scene"], result["hero_score"], row["id"]),
        )
        conn.commit()
        # Meter the call if it was a billed cloud one (the cloud backend attached a
        # usage_meta; local/argus don't), so per-album COGS is queryable.
        meta = result.get("usage_meta")
        if meta:
            usage.record(
                conn, album_id=album_id, photo_id=row["id"], stage="vision", **meta
            )
        n += 1
    return n
