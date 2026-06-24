"""Auto-attach Plutus offer URLs when an album finishes building."""
from __future__ import annotations

import logging
import sqlite3

from mnemosyne import config, plutus_api, plutus_link

log = logging.getLogger("mnemosyne.plutus_auto")


def maybe_attach_offer(conn: sqlite3.Connection, album_id: int) -> str | None:
    """Mint and persist a Plutus offer when configured and a run id is available.

    Best-effort: failures are logged and never fail the album build. Returns the
    normalized offer URL when attached, else None.
    """
    if not config.PLUTUS_AUTO_LINK:
        return None
    if not plutus_api.configured():
        return None

    row = conn.execute(
        "SELECT plutus_offer_url, plutus_run_id, name FROM albums WHERE id = ?",
        (album_id,),
    ).fetchone()
    if row is None or row["plutus_offer_url"]:
        return None

    run_id = row["plutus_run_id"]
    if run_id is None and config.PLUTUS_DEFAULT_RUN_ID:
        try:
            run_id = int(config.PLUTUS_DEFAULT_RUN_ID)
        except (TypeError, ValueError):
            run_id = None
    if not run_id:
        return None

    try:
        offer = plutus_api.create_offer_url(run_id=int(run_id), label=row["name"])
    except plutus_api.PlutusApiError as exc:
        log.warning("album %s plutus auto-link failed: %s", album_id, exc)
        return None

    normalized = plutus_link.normalize_offer_url(offer)
    if not normalized:
        log.warning("album %s plutus auto-link returned invalid URL", album_id)
        return None

    cur = conn.execute(
        "UPDATE albums SET plutus_offer_url = ? WHERE id = ? AND plutus_offer_url IS NULL",
        (normalized, album_id),
    )
    conn.commit()
    if cur.rowcount == 1:
        log.info("album %s plutus offer attached (run %s)", album_id, run_id)
        return normalized
    return None