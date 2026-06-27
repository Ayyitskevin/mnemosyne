"""Proposal — emit Mnemosyne's layout as the strict Worker-Contract JSON.

Mnemosyne's role in Mise Solo Studio OS is to *propose* a curated, ordered set of
spread placements; Mise's deterministic validator is authoritative and a human
approves every layout before any print/export. This module is the contract
boundary. It serializes the spreads/placements Mnemosyne already built into the
exact JSON Mise re-validates, and ships a strict validator that mirrors Mise's
rules — so Mnemosyne never emits a proposal Mise would reject, and can never
silently omit, duplicate, or misassign a photo.

Shape (the structured-output contract):

    {"placements": [{"asset_id": int, "spread": int>=0, "slot": int>=0}, ...],
     "provider": "...", "model": "...", "notes": "optional"}

`spread` and `slot` are 0-based at this boundary (Mise's contract), even though
Mnemosyne stores spreads (position) and slots 1-based internally. The serializer
densifies both — it maps the album's ordered spreads to 0..k-1 and each spread's
ordered slots to 0..m-1 — so the emitted indices are contiguous and collision-free
regardless of any gaps left by manual nudges.

`asset_id` references the gallery's asset id, and a proposal uses ONE id space
throughout — mixing them could let a local id collide with another photo's Mise id
and read as a duplicate placement. So when every photo in the album carries Mise's
id (`photos.mise_asset_id`), the proposal reports those; otherwise (upload albums,
legacy rows, or a Mise import only partially matched) it reports the local
`photos.id` for the whole album. `_use_mise_ids` decides once and both the
serializer and the eligibility set read the same choice.
"""
from __future__ import annotations

import sqlite3

from mnemosyne import runtime


def _use_mise_ids(conn: sqlite3.Connection, album_id: int) -> bool:
    """True when EVERY photo in the album carries a Mise asset id, so the whole
    proposal can safely report Mise's id space. Falls to local ids otherwise, which
    keeps a single, collision-free id space rather than mixing the two."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, COUNT(mise_asset_id) AS with_mise "
        "FROM photos WHERE album_id = ?",
        (album_id,),
    ).fetchone()
    return row["total"] > 0 and row["with_mise"] == row["total"]


def _asset_id_col(use_mise: bool, prefix: str = "") -> str:
    """The column the proposal reports as `asset_id` — Mise's id when the whole
    album is Mise-mapped (guaranteed non-NULL then), else the local row id. `prefix`
    is the table alias (e.g. "p.") when the query joins photos under one."""
    return f"{prefix}mise_asset_id" if use_mise else f"{prefix}id"


class ProposalError(Exception):
    """Mnemosyne built a proposal that fails its own mirror of Mise's validator.
    Raised by build_proposal rather than emitting something Mise would reject."""


def _is_int(value: object) -> bool:
    """True for a real integer. bool is an int subclass, so exclude it — a
    True/False slipping in as an asset_id or index would be a silent corruption."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_proposal(
    proposal: object, eligible_asset_ids: set[int] | None = None
) -> list[str]:
    """Mnemosyne's local mirror of Mise's authoritative validator. Returns a list
    of human-readable errors — empty means the proposal would pass Mise.

    Checks the structural contract: a placements list plus non-empty provider/model
    strings; every placement an object with integer asset_id and integer spread/slot
    both >= 0; each asset placed at most once; each (spread, slot) used at most once.
    When `eligible_asset_ids` is given, every asset_id must be in it (a placement may
    only reference a photo that belongs to THIS gallery and is processed/ready);
    pass None to skip that check and validate shape alone.
    """
    errors: list[str] = []
    if not isinstance(proposal, dict):
        return ["proposal must be a JSON object"]

    for field in ("provider", "model"):
        value = proposal.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{field} must be a non-empty string")
    if "notes" in proposal and not isinstance(proposal["notes"], str):
        errors.append("notes must be a string when present")

    placements = proposal.get("placements")
    if not isinstance(placements, list):
        errors.append("placements must be a list")
        return errors

    seen_assets: set[int] = set()
    seen_slots: set[tuple[int, int]] = set()
    for i, pl in enumerate(placements):
        if not isinstance(pl, dict):
            errors.append(f"placement[{i}] must be an object")
            continue
        aid, spread, slot = pl.get("asset_id"), pl.get("spread"), pl.get("slot")

        if not _is_int(aid):
            errors.append(f"placement[{i}].asset_id must be an integer")
        else:
            if eligible_asset_ids is not None and aid not in eligible_asset_ids:
                errors.append(
                    f"placement[{i}].asset_id {aid} is not an eligible gallery asset"
                )
            if aid in seen_assets:
                errors.append(f"asset {aid} is placed more than once")
            seen_assets.add(aid)

        if not _is_int(spread) or spread < 0:
            errors.append(f"placement[{i}].spread must be an integer >= 0")
        if not _is_int(slot) or slot < 0:
            errors.append(f"placement[{i}].slot must be an integer >= 0")
        if _is_int(spread) and _is_int(slot) and spread >= 0 and slot >= 0:
            key = (spread, slot)
            if key in seen_slots:
                errors.append(f"(spread {spread}, slot {slot}) is used more than once")
            seen_slots.add(key)

    return errors


def eligible_asset_ids(
    conn: sqlite3.Connection, album_id: int, *, use_mise: bool | None = None
) -> set[int]:
    """The gallery assets a placement is allowed to reference. For the standalone
    (upload) album this is every photo in the album that the look step has scored —
    i.e. processed/ready, mirroring Mise's eligibility. (The Mise-import path will
    narrow this to Mise's own processed set once per-asset signals are read.)

    Pass `use_mise` to reuse an id-space decision already taken for the matching
    build_proposal call: building the proposal and computing its eligibility are two
    DB reads, and if a Mise import stamps the album's last mise_asset_id between them
    the recomputed decision could flip, leaving the two sides in different id spaces.
    A shared `use_mise` keeps them in lockstep."""
    if use_mise is None:
        use_mise = _use_mise_ids(conn, album_id)
    col = _asset_id_col(use_mise)
    rows = conn.execute(
        f"SELECT {col} AS asset_id FROM photos "
        "WHERE album_id = ? AND scene IS NOT NULL",
        (album_id,),
    ).fetchall()
    return {r["asset_id"] for r in rows}


def build_proposal(
    conn: sqlite3.Connection,
    album_id: int,
    *,
    notes: str | None = None,
    use_mise: bool | None = None,
) -> dict:
    """Serialize an album's spreads/placements into the strict proposal JSON.

    Reads the layout Mnemosyne already committed and emits 0-based, densified
    spread/slot indices with each asset placed once. Validates the result against
    `validate_proposal` before returning, so a malformed proposal raises
    ProposalError here rather than reaching Mise — defense in depth for the
    never-silently-omit/duplicate/misassign guardrail.

    `use_mise` pins the id-space decision; pass the same value to a paired
    eligible_asset_ids call so the proposal and its eligibility set can't drift into
    different id spaces if a Mise import commits between the two reads.
    """
    if use_mise is None:
        use_mise = _use_mise_ids(conn, album_id)
    spread_rows = conn.execute(
        "SELECT id FROM spreads WHERE album_id = ? ORDER BY position, id",
        (album_id,),
    ).fetchall()

    col = _asset_id_col(use_mise, "p.")
    placements: list[dict] = []
    for spread_idx, spread in enumerate(spread_rows):
        photo_rows = conn.execute(
            f"SELECT {col} AS asset_id FROM placements pl "
            "JOIN photos p ON p.id = pl.photo_id "
            "WHERE pl.spread_id = ? ORDER BY pl.slot, pl.id",
            (spread["id"],),
        ).fetchall()
        for slot_idx, row in enumerate(photo_rows):
            placements.append(
                {"asset_id": row["asset_id"], "spread": spread_idx, "slot": slot_idx}
            )

    status = runtime.backend_status()
    proposal: dict = {
        "placements": placements,
        "provider": runtime.arrange_backend(),
        "model": str(status["arrange_model"]),
    }
    if notes:
        proposal["notes"] = notes

    errors = validate_proposal(proposal)
    if errors:
        raise ProposalError("; ".join(errors))
    return proposal
