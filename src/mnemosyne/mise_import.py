"""Import a Mise gallery into mnemosyne — copy or reference originals, enqueue album."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mnemosyne import config, ingest, mise_client, pipeline
from mnemosyne.themes import normalize_theme

IMAGE_SUFFIXES = ingest.IMAGE_SUFFIXES


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