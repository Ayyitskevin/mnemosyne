"""Show — the web station. A thin reader over the album data.

Boots, applies migrations, and serves a public landing page (`/`, the demand-
validation pitch + waitlist signup) plus the local album tool: an index of albums
(`/albums`) and a spread-by-spread preview of one album. Photos are served
straight off disk from the path recorded at ingest (the album tool is still
single-user and local, so reading our own DB-stored paths is fine — auth and
multi-tenancy arrive with the Phase 2 SaaS turn).
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import os
import tempfile

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

from mnemosyne import albums, config, db, edits, export, waitlist

TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "templates")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    conn.close()
    yield


app = FastAPI(title="mnemosyne", lifespan=lifespan)


def get_conn() -> sqlite3.Connection:
    conn = db.connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """The public pitch. No data of its own — its only job is to make the promise
    and capture an email, so it needs no DB read."""
    return TEMPLATES.TemplateResponse(request, "landing.html", {})


@app.post("/waitlist", response_class=HTMLResponse)
def join_waitlist(
    request: Request,
    email: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Capture a signup, then re-render the landing page with the outcome.

    Validation happens here, at the boundary, before we trust the input. A bad
    address re-renders with an error and the typed value preserved; a good one is
    added idempotently and we show the on-the-list confirmation. Plain form POST
    with a full re-render — no JS, matching the rest of the app.
    """
    if not waitlist.is_valid_email(email):
        return TEMPLATES.TemplateResponse(
            request,
            "landing.html",
            {"error": "That doesn't look like an email address.", "email": email},
        )
    waitlist.add(conn, email, source="landing")
    return TEMPLATES.TemplateResponse(request, "landing.html", {"joined": True})


@app.get("/albums", response_class=HTMLResponse)
def index(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return TEMPLATES.TemplateResponse(
        request, "albums.html", {"albums": albums.list_albums(conn)}
    )


@app.get("/albums/{album_id}", response_class=HTMLResponse)
def show_album(
    album_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    data = albums.album_for_render(conn, album_id)
    if data is None:
        raise HTTPException(status_code=404, detail="no such album")
    return TEMPLATES.TemplateResponse(
        request,
        "album.html",
        {"album": data["album"], "spreads": data["spreads"]},
    )


@app.post("/albums/{album_id}/spreads/{spread_id}/move/{direction}")
def move_spread(
    album_id: int,
    spread_id: int,
    direction: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    edits.move_spread(conn, album_id, spread_id, direction)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/{album_id}/spreads/{spread_id}/hero/{photo_id}")
def set_hero(
    album_id: int,
    spread_id: int,
    photo_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
):
    edits.set_hero(conn, album_id, spread_id, photo_id)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/{album_id}/spreads/{spread_id}/photos/{photo_id}/move/{direction}")
def move_photo(
    album_id: int,
    spread_id: int,
    photo_id: int,
    direction: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    edits.move_photo(conn, album_id, spread_id, photo_id, direction)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.get("/albums/{album_id}/pdf")
def album_pdf(album_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        export.export_album(conn, album_id, tmp)
        pdf = Path(tmp).read_bytes()
    except LookupError:
        raise HTTPException(status_code=404, detail="no such album")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="album-{album_id}.pdf"'},
    )


@app.get("/photo/{photo_id}")
def photo_file(photo_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    photo = albums.get_photo(conn, photo_id)
    if photo is None or not Path(photo["path"]).is_file():
        raise HTTPException(status_code=404, detail="no such photo")
    return FileResponse(photo["path"])
