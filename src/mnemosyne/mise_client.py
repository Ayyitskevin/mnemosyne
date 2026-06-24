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