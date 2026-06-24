"""Plutus API client — mint storefront offer links from a bundles run id."""
from __future__ import annotations

import httpx2 as httpx

from mnemosyne import config, plutus_link


class PlutusApiError(Exception):
    pass


def configured() -> bool:
    return bool(config.PLUTUS_URL and config.PLUTUS_API_TOKEN and config.PLUTUS_TENANT_ID)


def create_offer_url(*, run_id: int, label: str | None = None) -> str:
    """Call Plutus POST /integrations/offer and return a normalized offer URL."""
    base = (config.PLUTUS_URL or "").rstrip("/")
    token = config.PLUTUS_API_TOKEN
    tenant_id = config.PLUTUS_TENANT_ID
    if not base or not token or not tenant_id:
        raise PlutusApiError(
            "MNEMOSYNE_PLUTUS_URL, MNEMOSYNE_PLUTUS_API_TOKEN, and "
            "MNEMOSYNE_PLUTUS_TENANT_ID required"
        )

    data = {"run_id": str(run_id), "tenant_id": tenant_id}
    if label:
        data["label"] = label

    url = f"{base}/integrations/offer"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                url,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        raise PlutusApiError(f"Plutus API failed: {exc}") from exc

    raw = body.get("public_url") or body.get("url")
    if not raw:
        raise PlutusApiError("Plutus response missing offer URL")
    normalized = plutus_link.normalize_offer_url(raw)
    if not normalized:
        raise PlutusApiError("Plutus returned an invalid offer URL")
    return normalized