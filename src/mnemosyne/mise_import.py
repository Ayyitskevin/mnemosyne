"""Import a Mise gallery into mnemosyne — copy or reference originals, enqueue album."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from mnemosyne import config, ingest, mise_client, pipeline
from mnemosyne.themes import normalize_theme

IMAGE_SUFFIXES = ingest.IMAGE_SUFFIXES

log = logging.getLogger("mnemosyne.mise_import")


class MiseImportError(Exception):
    pass


def _count_images(path: Path) -> int:
    return sum(1 for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def resolve_originals_dir(gallery: dict) -> Path:
    """Pick a readable originals folder for a Mise gallery row."""
    gid = gallery.get("id")
    candidates: list[Path] = []
    if config.MISE_MEDIA_ROOT and gid is not None:
        candidates.append(config.MISE_MEDIA_ROOT / str(gid) / "original")
    raw = gallery.get("originals_path")
    if raw:
        candidates.append(Path(str(raw)).expanduser())
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and _count_images(resolved) > 0:
            return resolved
    raise MiseImportError(
        "Gallery originals not found locally — sync media (see scripts/sync-mise-media.sh "
        "in plutus/argus) or set MNEMOSYNE_MISE_MEDIA_ROOT"
    )


def _album_exists_for_mise(
    conn: sqlite3.Connection, owner_id: int, gallery_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 AS x FROM albums WHERE owner_id = ? AND mise_gallery_id = ? LIMIT 1",
        (owner_id, gallery_id),
    ).fetchone()
    return row is not None


def import_gallery(
    conn: sqlite3.Connection,
    *,
    owner_id: int,
    gallery_id: int,
    gallery_theme: str = "food",
    allow_duplicate: bool = False,
) -> int:
    """Fetch a Mise gallery, stage photos, enqueue a pending mnemosyne album."""
    if not mise_client.configured():
        raise MiseImportError(
            "MNEMOSYNE_MISE_URL and MNEMOSYNE_MISE_API_TOKEN required"
        )
    if not allow_duplicate and _album_exists_for_mise(conn, owner_id, gallery_id):
        raise MiseImportError(f"Gallery {gallery_id} was already imported")

    gallery = mise_client.get_gallery(gallery_id)
    if gallery is None:
        raise MiseImportError(f"Mise gallery {gallery_id} not found")

    source_dir = resolve_originals_dir(gallery)
    name = (gallery.get("title") or "").strip() or f"Mise gallery {gallery_id}"
    run_id = gallery.get("plutus_last_run_id")
    try:
        run_id = int(run_id) if run_id is not None else None
    except (TypeError, ValueError):
        run_id = None

    return pipeline.enqueue_album(
        conn,
        name=name,
        source_dir=source_dir,
        owner_id=owner_id,
        gallery_theme=normalize_theme(gallery_theme),
        mise_gallery_id=gallery_id,
        plutus_run_id=run_id,
    )


def _basename(storage_key: str) -> str:
    """The original filename behind a photo's storage key (`a<album>/<file>`)."""
    return str(storage_key).rsplit("/", 1)[-1]


def apply_mise_signals(conn: sqlite3.Connection, album_id: int) -> dict:
    """Stamp Mise's per-asset identity + culling signals onto this album's photos.

    The contract is to *consume* Mise's stored hero/keeper scores, not recompute
    vision. This runs after ingest (the photo rows exist) and before the look step,
    matching each photo to a Mise asset by filename:

      * mise_asset_id and keeper_score are set whenever Mise supplies them, so the
        proposal references Mise's id space and a later cull can read keeper_score —
        regardless of whether vision still runs for that photo.
      * scene + hero_score are adopted from Mise ONLY when Mise supplies BOTH a scene
        label and a hero_potential (a complete signal). That sets `scene`, which makes
        the look step skip the photo (it only scores rows with scene IS NULL) — so
        Mise's hero score is consumed, never recomputed or half-overwritten. When the
        signal is incomplete, vision fills scene + hero locally for that photo.

    Stateless + safe by construction: a no-op for non-Mise albums, and ANY Mise API
    failure is swallowed with a logged warning so the build falls back to local vision
    rather than failing. Returns a small summary for observability.
    """
    summary = {"matched": 0, "signals_adopted": 0, "ids_only": 0}
    row = conn.execute(
        "SELECT mise_gallery_id FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    gallery_id = row["mise_gallery_id"] if row else None
    if gallery_id is None:
        return summary  # standalone (upload) album — nothing to read from Mise
    if not mise_client.configured():
        return summary

    try:
        assets = mise_client.list_assets(int(gallery_id))
    except mise_client.MiseClientError as exc:
        log.warning(
            "album %s: Mise signal read failed (%s) — falling back to local vision",
            album_id,
            exc,
        )
        return summary

    by_name = {a["filename"]: a for a in assets if a.get("filename")}
    if not by_name:
        return summary

    photos = conn.execute(
        "SELECT id, storage_key FROM photos WHERE album_id = ?", (album_id,)
    ).fetchall()
    for photo in photos:
        asset = by_name.get(_basename(photo["storage_key"]))
        if asset is None:
            continue
        summary["matched"] += 1
        complete = asset.get("scene") and asset.get("hero_potential") is not None
        if complete:
            conn.execute(
                "UPDATE photos SET mise_asset_id = ?, keeper_score = ?, "
                "scene = ?, hero_score = ? WHERE id = ?",
                (
                    asset.get("asset_id"),
                    asset.get("keeper_score"),
                    asset["scene"],
                    asset["hero_potential"],
                    photo["id"],
                ),
            )
            summary["signals_adopted"] += 1
        else:
            # Carry Mise's id (+ keeper if present) but let vision score this photo,
            # so a partial signal never leaves it with a hero score and no scene.
            conn.execute(
                "UPDATE photos SET mise_asset_id = ?, keeper_score = ? WHERE id = ?",
                (asset.get("asset_id"), asset.get("keeper_score"), photo["id"]),
            )
            summary["ids_only"] += 1
    conn.commit()
    log.info("album %s: applied Mise signals %s", album_id, summary)
    return summary