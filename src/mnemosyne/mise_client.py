"""Read-only Mise gallery index (same bearer as MISE_ARGUS_TOKEN on flow)."""
from __future__ import annotations

import logging
from typing import Any

import httpx2 as httpx

from mnemosyne import config

log = logging.getLogger("mnemosyne.mise")


class MiseClientError(Exception):
    """Human-readable Mise API failure."""


def configured() -> bool:
    return bool(config.MISE_URL and config.MISE_API_TOKEN)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.MISE_API_TOKEN}"}


def list_galleries(*, published: bool | None = None) -> dict[str, Any]:
    if not configured():
        raise MiseClientError("Mise API is not configured")
    url = f"{config.MISE_URL}/api/galleries"
    params: dict[str, str] = {}
    if published is not None:
        params["published"] = "true" if published else "false"
    try:
        with httpx.Client(timeout=config.MISE_TIMEOUT) as client:
            resp = client.get(url, params=params or None, headers=_headers())
    except httpx.TimeoutException as exc:
        raise MiseClientError(f"Mise API timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise MiseClientError(f"Mise API unreachable: {exc}") from exc

    if resp.status_code == 503:
        raise MiseClientError("Mise galleries API is disarmed")
    if resp.status_code == 401:
        raise MiseClientError("Mise API rejected the bearer token")
    if resp.status_code >= 400:
        raise MiseClientError(f"Mise API returned HTTP {resp.status_code}")

    body = resp.json()
    if not isinstance(body, dict) or "galleries" not in body:
        raise MiseClientError("Mise API returned an unexpected body")
    return body


def get_gallery(gallery_id: int) -> dict[str, Any] | None:
    body = list_galleries(published=False)
    for row in body.get("galleries") or []:
        if row.get("id") == gallery_id:
            return row
    return None


def _coerce_unit(value: Any) -> float | None:
    """A signal coerced to a 0..1 float, or None when absent/unparseable. Mise is
    authoritative for these; we only clamp so a stray out-of-range value can't poison
    downstream ranking (Rule 5: the source judges, code keeps it in-bounds)."""
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _normalize_asset(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map ONE Mise asset row to the fields the importer needs, tolerant of the
    exact key names so a small Mise-side rename doesn't silently drop signals. This
    is the single place to adjust if Mise's real field names differ. Returns None
    when the row has neither an id nor a filename to match on — nothing to use.

    Recognized shapes (first hit wins):
      asset_id : id | asset_id
      filename : filename | original_filename | name | basename(path|storage_key)
      hero     : hero_potential | culling.hero_potential   (Argus's nested shape)
      keeper   : keeper_score   | culling.keeper_score
      label    : scene | shot_type (+ first keyword)       -> short scene string
      processed: processed | ready | status == 'ready'      (default True)
    """
    if not isinstance(raw, dict):
        return None
    culling = raw.get("culling") if isinstance(raw.get("culling"), dict) else {}

    asset_id = raw.get("id")
    if asset_id is None:
        asset_id = raw.get("asset_id")
    try:
        asset_id = int(asset_id) if asset_id is not None else None
    except (TypeError, ValueError):
        asset_id = None

    filename = (
        raw.get("filename")
        or raw.get("original_filename")
        or raw.get("name")
    )
    if not filename:
        keyish = raw.get("path") or raw.get("storage_key")
        filename = str(keyish).rsplit("/", 1)[-1] if keyish else None
    filename = str(filename) if filename else None

    if asset_id is None and not filename:
        return None

    hero = _coerce_unit(
        raw.get("hero_potential", culling.get("hero_potential"))
    )
    keeper = _coerce_unit(
        raw.get("keeper_score", culling.get("keeper_score"))
    )

    label = raw.get("scene") or raw.get("shot_type")
    if label:
        keywords = raw.get("keywords") or []
        if isinstance(keywords, list) and keywords:
            label = f"{label} {keywords[0]}"
        label = str(label).strip()[:120]
    else:
        label = None

    processed = raw.get("processed")
    if processed is None:
        processed = raw.get("ready")
    if processed is None:
        status = raw.get("status")
        processed = (status == "ready") if status is not None else True

    return {
        "asset_id": asset_id,
        "filename": filename,
        "hero_potential": hero,
        "keeper_score": keeper,
        "scene": label,
        "processed": bool(processed),
    }


def list_assets(gallery_id: int) -> list[dict[str, Any]]:
    """Read-only per-asset signals for one Mise gallery, normalized for import.

    Hits config.MISE_ASSETS_PATH (a {gallery_id} template under MISE_URL) and accepts
    any of {"assets": [...]}, {"photos": [...]}, or a bare list. Raises
    MiseClientError on a transport/HTTP/shape failure so the caller can fall back to
    local vision rather than guess — this never silently returns an empty list for an
    error (that would read as 'gallery has no assets', hiding a real outage)."""
    if not configured():
        raise MiseClientError("Mise API is not configured")
    path = config.MISE_ASSETS_PATH.format(gallery_id=gallery_id)
    url = f"{config.MISE_URL.rstrip('/')}{path if path.startswith('/') else '/' + path}"
    try:
        with httpx.Client(timeout=config.MISE_TIMEOUT) as client:
            resp = client.get(url, headers=_headers())
    except httpx.TimeoutException as exc:
        raise MiseClientError(f"Mise assets API timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise MiseClientError(f"Mise assets API unreachable: {exc}") from exc

    if resp.status_code == 401:
        raise MiseClientError("Mise API rejected the bearer token")
    if resp.status_code == 404:
        raise MiseClientError("Mise assets endpoint not found (check MNEMOSYNE_MISE_ASSETS_PATH)")
    if resp.status_code >= 400:
        raise MiseClientError(f"Mise assets API returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise MiseClientError("Mise assets API returned non-JSON") from exc

    if isinstance(body, dict):
        rows = body.get("assets")
        if rows is None:
            rows = body.get("photos")
    else:
        rows = body
    if not isinstance(rows, list):
        raise MiseClientError("Mise assets API returned an unexpected body")

    return [a for a in (_normalize_asset(r) for r in rows) if a is not None]