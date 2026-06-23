"""Meter — record what each billed cloud-inference call cost.

Phase 2 moves the "look" (vision) and "arrange" (reasoning) steps off mickey's
free local fleet onto a metered cloud API, which turns every album into a real
$ of COGS. Pricing is downstream of that number, so it has to be OBSERVABLE, not a
surprise on the monthly bill (CLAUDE.md R14). This station writes one row per
billed call into `inference_usage` and rolls them up per album.

The token counts are the ground truth — they come straight from the vendor's
`usage` block. Dollars are DERIVED from configurable per-million rates, and are
NULL until those rates are set in .env, so an unpriced install reports honest
"unknown cost" rather than a fake $0. Only the cloud backends call in here; local
Ollama is free and never recorded.
"""
from __future__ import annotations

import sqlite3

from mnemosyne import config


def _cost_usd(prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    """Dollar cost of one call from configured per-1M-token rates, or None when no
    rate is set (tokens stay the truth; we never invent a price). Unknown token
    counts count as zero for the side that's missing rather than poisoning the sum."""
    pp = config.GROK_PRICE_PROMPT_PER_M
    cp = config.GROK_PRICE_COMPLETION_PER_M
    if not pp and not cp:
        return None
    return ((prompt_tokens or 0) * pp + (completion_tokens or 0) * cp) / 1_000_000


def record(
    conn: sqlite3.Connection,
    *,
    album_id: int,
    photo_id: int | None,
    stage: str,
    backend: str,
    model: str,
    tokens: dict,
    latency: float | None,
) -> None:
    """Append one billed-call row. `tokens` is the vendor's usage block
    ({prompt_tokens, completion_tokens, total_tokens}); missing keys store NULL.
    Best-effort by contract: metering must never crash an album build, so a write
    failure is swallowed (the file log in the backend remains the backstop)."""
    pt = tokens.get("prompt_tokens")
    ct = tokens.get("completion_tokens")
    tt = tokens.get("total_tokens")
    try:
        conn.execute(
            "INSERT INTO inference_usage (album_id, photo_id, stage, backend, model, "
            "prompt_tokens, completion_tokens, total_tokens, latency_s, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (album_id, photo_id, stage, backend, model, pt, ct, tt, latency,
             _cost_usd(pt, ct)),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def album_summary(conn: sqlite3.Connection, album_id: int) -> dict:
    """COGS roll-up for one album: how many billed calls, total tokens, and total
    dollars (None if any call was unpriced — so a partial price never reads as the
    whole truth). This is what a 'what did this album cost me' view reads."""
    row = conn.execute(
        "SELECT COUNT(*) AS calls, "
        "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
        "SUM(cost_usd) AS cost_usd, "
        "COUNT(cost_usd) AS priced_calls "
        "FROM inference_usage WHERE album_id = ?",
        (album_id,),
    ).fetchone()
    calls = row["calls"]
    # A dollar total is only honest if EVERY call was priced; otherwise report it as
    # unknown rather than summing a subset.
    cost = row["cost_usd"] if calls and row["priced_calls"] == calls else None
    return {
        "calls": calls,
        "total_tokens": row["total_tokens"],
        "cost_usd": cost,
    }
