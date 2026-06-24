"""Plutus storefront cross-sell — link a finished album to print bundles."""
from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

from mnemosyne import config


def normalize_offer_url(raw: str | None) -> str | None:
    """Accept a full HTTPS offer URL or a path when MNEMOSYNE_PLUTUS_URL is set."""
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None
    if url.startswith("/"):
        base = (config.PLUTUS_URL or "").rstrip("/")
        return f"{base}{url}" if base else None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if "/store/" not in parsed.path or "/offer/" not in parsed.path:
        return None
    return url.rstrip("/") if url.endswith("/") else url


def save_offer_url(
    conn: sqlite3.Connection, album_id: int, owner_id: int, raw: str | None
) -> str | None:
    """Persist a normalized Plutus offer URL for an owned album."""
    url = normalize_offer_url(raw)
    cur = conn.execute(
        "UPDATE albums SET plutus_offer_url = ? WHERE id = ? AND owner_id = ?",
        (url, album_id, owner_id),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return url


def offer_cta_label() -> str:
    return "Order prints"