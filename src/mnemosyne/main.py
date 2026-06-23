"""Show — the web station. A thin reader over the album data.

Boots, applies migrations, and serves two pages: an index of albums and a spread-
by-spread preview of one album. Photos are served straight off disk from the path
recorded at ingest (Phase 0 is single-user and local, so reading our own DB-stored
paths is fine).
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import os
import tempfile

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from mnemosyne import albums, config, db, export

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
