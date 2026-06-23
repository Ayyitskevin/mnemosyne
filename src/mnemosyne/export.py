"""Export — render a laid-out album to a print-ready PDF.

Reuses the exact geometry the web preview uses: albums.album_for_render gives each
spread a layout plan (fractional column/row tracks + a grid-template-areas map),
and this station resolves those fr-tracks into absolute rectangles on a landscape
page, then draws each photo cover-cropped into its rectangle. One spread per page.

Deterministic by design (Rule 5): the creative calls — which photos share a spread
and which is the hero — were made upstream. Turning fr-units into points is pure
arithmetic, so this is plain code, mirroring layout.py rather than a model call.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from mnemosyne import albums, storage

# A page is one spread at the preview's 16:10 aspect; points (1/72").
PAGE_W, PAGE_H = 864.0, 540.0
MARGIN = 28.0
CAPTION_H = 20.0
GAP = 5.0  # matches the .pages grid gap in album.html

_INK = (0x0E / 255, 0x0D / 255, 0x0C / 255)   # page background
_GUTTER = (0, 0, 0)                            # gaps between photos
_LABEL = (0x9B / 255, 0x91 / 255, 0x83 / 255)  # muted caption text


def _parse_tracks(spec: str) -> list[float]:
    """'1.3fr 1fr' -> [1.3, 1.0]. Unitless weights; the caller normalizes."""
    return [float(tok.replace("fr", "")) for tok in spec.split()]


def _parse_areas(spec: str) -> list[list[str]]:
    '''\'"a a" "b c"\' -> [["a", "a"], ["b", "c"]] (the grid as a name matrix).'''
    return [row.split() for row in re.findall(r'"([^"]*)"', spec)]


def _offsets(weights: list[float], total: float, gap: float) -> list[float]:
    """Per-track sizes filling `total` (gaps between tracks subtracted first)."""
    inner = total - gap * (len(weights) - 1)
    unit = inner / sum(weights)
    return [w * unit for w in weights]


def _area_rects(layout: dict, x: float, y_top: float, w: float, h: float) -> dict:
    """Map each grid area name to an (x, y, w, h) rect in reportlab coords
    (origin bottom-left). Areas in our templates are always rectangular blocks,
    so an area's rect is the bounding box of the cells carrying its name."""
    cols = _offsets(_parse_tracks(layout["cols"]), w, GAP)
    rows = _offsets(_parse_tracks(layout["rows"]), h, GAP)
    matrix = _parse_areas(layout["areas"])

    # Left edge of each column / top offset of each row (with gaps baked in).
    col_x = [x]
    for cw in cols[:-1]:
        col_x.append(col_x[-1] + cw + GAP)
    row_top = [0.0]
    for rh in rows[:-1]:
        row_top.append(row_top[-1] + rh + GAP)

    rects: dict[str, tuple[float, float, float, float]] = {}
    names = {name for line in matrix for name in line}
    for name in names:
        cells = [(r, c) for r, line in enumerate(matrix)
                 for c, n in enumerate(line) if n == name]
        rmin = min(r for r, _ in cells)
        rmax = max(r for r, _ in cells)
        cmin = min(c for _, c in cells)
        cmax = max(c for _, c in cells)
        ax = col_x[cmin]
        aw = (col_x[cmax] + cols[cmax]) - ax
        top = row_top[rmin]
        ah = (row_top[rmax] + rows[rmax]) - top
        ay = y_top - top - ah  # flip: grid grows down, reportlab grows up
        rects[name] = (ax, ay, aw, ah)
    return rects


def _cover(img: Image.Image, w: float, h: float) -> Image.Image:
    """object-fit: cover — scale to fill w×h, centre-crop the overflow."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    cw, ch = w / scale, h / scale
    left, top = (iw - cw) / 2, (ih - ch) / 2
    return img.crop((round(left), round(top), round(left + cw), round(top + ch)))


def _draw_photo(c: canvas.Canvas, key: str, rect: tuple) -> None:
    x, y, w, h = rect
    # Pull the bytes via the storage seam, not the filesystem, so export keeps
    # working when photos live in an object store. The image is fully read inside
    # this block, so a remote driver's temp file is safe to clean up on exit.
    store = storage.get_storage()
    with store.open_path(key) as path, Image.open(path) as img:
        cropped = _cover(img.convert("RGB"), w, h)
        c.drawImage(ImageReader(cropped), x, y, w, h)


def export_album(conn: sqlite3.Connection, album_id: int, out_path: str | Path) -> str:
    """Render the album to a one-spread-per-page PDF at `out_path`. Returns the
    path written. Raises LookupError if the album doesn't exist."""
    data = albums.album_for_render(conn, album_id)
    if data is None:
        raise LookupError(f"no such album: {album_id}")

    out_path = str(out_path)
    c = canvas.Canvas(out_path, pagesize=(PAGE_W, PAGE_H))
    name = data["album"]["name"]

    for spread in data["spreads"]:
        c.setFillColorRGB(*_INK)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        sx, sw = MARGIN, PAGE_W - 2 * MARGIN
        sh = PAGE_H - 2 * MARGIN - CAPTION_H
        y_top = PAGE_H - MARGIN
        c.setFillColorRGB(*_GUTTER)
        c.rect(sx, y_top - sh, sw, sh, fill=1, stroke=0)

        rects = _area_rects(spread["layout"], sx, y_top, sw, sh)
        for slot in spread["layout"]["slots"]:
            _draw_photo(c, slot["photo"]["storage_key"], rects[slot["area"]])

        # Faint spine down the centre, mirroring the preview.
        c.saveState()
        c.setStrokeColorRGB(1, 1, 1)
        c.setStrokeAlpha(0.07)
        c.setLineWidth(0.5)
        c.line(sx + sw / 2, y_top - sh, sx + sw / 2, y_top)
        c.restoreState()

        c.setFillColorRGB(*_LABEL)
        c.setFont("Helvetica", 8)
        c.drawString(MARGIN, MARGIN - 2, name)
        c.drawRightString(PAGE_W - MARGIN, MARGIN - 2,
                          f"Spread {spread['position']}")
        c.showPage()

    c.save()
    return out_path
