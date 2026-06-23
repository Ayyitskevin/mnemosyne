"""Show — the web station. A thin reader over the album data.

Boots, applies migrations, and serves a public landing page (`/`, the demand-
validation pitch + waitlist signup) plus the per-account album tool: signup/login,
an index of the logged-in user's albums (`/albums`), and a spread-by-spread preview
of one album. Album and photo routes require a session and are scoped to the
owner — you only ever see your own galleries, and a cross-tenant id 404s rather
than confirming the album exists. Photos are served off disk from the path
recorded at ingest, gated through the same owner check.
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
from fastapi.exception_handlers import http_exception_handler
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from mnemosyne import albums, auth, config, db, edits, export, waitlist

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
# Signed-cookie sessions. We only ever store the user id ("uid"); everything else
# about the user is re-read from the DB per request, so a stale cookie can't carry
# stale account state — at worst it points at a since-deleted id, which resolves
# to "logged out".
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)


def get_conn() -> sqlite3.Connection:
    conn = db.connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def current_user(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> dict | None:
    """The logged-in user row, or None. Reads the id from the signed session and
    re-fetches the row, so authorization always reflects the live DB, not the
    cookie. Public routes call this to greet a user; protected routes go through
    require_user instead."""
    uid = request.session.get("uid")
    if uid is None:
        return None
    return auth.get_user_by_id(conn, uid)


def require_user(user: dict | None = Depends(current_user)) -> dict:
    """Gate for the album tool: a real session or a 401. Bundling it as a
    dependency keeps every protected route one `Depends` away from the owner id,
    so no route can forget the check and leak across tenants."""
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    return user


@app.exception_handler(StarletteHTTPException)
async def _login_redirect(request: Request, exc: StarletteHTTPException):
    """Turn a 401 from require_user into a browser redirect to /login (this is a
    form app, not a JSON API — a sign-in bounce is the right UX). Everything else
    falls through to FastAPI's default handler unchanged."""
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return await http_exception_handler(request, exc)


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


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, user: dict | None = Depends(current_user)):
    if user is not None:
        return RedirectResponse("/albums", status_code=303)
    return TEMPLATES.TemplateResponse(request, "signup.html", {})


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Create an account and log the new user straight in. create_user is the
    validation boundary (bad/duplicate email -> ValueError), which we turn into a
    friendly re-render with the typed email preserved rather than a 500."""
    try:
        new_user = auth.create_user(conn, email, password)
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(
            request, "signup.html", {"error": str(exc), "email": email}
        )
    request.session["uid"] = new_user["id"]
    return RedirectResponse("/albums", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: dict | None = Depends(current_user)):
    if user is not None:
        return RedirectResponse("/albums", status_code=303)
    return TEMPLATES.TemplateResponse(request, "login.html", {})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Verify credentials and open a session. authenticate gives one outcome for
    'no such email' and 'wrong password', so the error message stays deliberately
    vague — telling them apart would let someone enumerate registered emails."""
    user = auth.authenticate(conn, email, password)
    if user is None:
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "Email or password is incorrect.", "email": email},
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/albums", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/albums", response_class=HTMLResponse)
def index(
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return TEMPLATES.TemplateResponse(
        request,
        "albums.html",
        {"albums": albums.list_albums(conn, user["id"]), "user": user},
    )


@app.get("/albums/{album_id}", response_class=HTMLResponse)
def show_album(
    album_id: int,
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    data = albums.album_for_render(conn, album_id, user["id"])
    if data is None:
        raise HTTPException(status_code=404, detail="no such album")
    return TEMPLATES.TemplateResponse(
        request,
        "album.html",
        {"album": data["album"], "spreads": data["spreads"], "user": user},
    )


def _require_owned_album(conn, album_id: int, user: dict) -> None:
    """Owner gate for the mutation/export routes. 404 (not 403) on a non-owned or
    missing album, so a stranger probing ids can't tell 'not yours' from 'doesn't
    exist'. Every write route below funnels through here before touching state."""
    if not albums.owns_album(conn, album_id, user["id"]):
        raise HTTPException(status_code=404, detail="no such album")


@app.post("/albums/{album_id}/spreads/{spread_id}/move/{direction}")
def move_spread(
    album_id: int,
    spread_id: int,
    direction: str,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    _require_owned_album(conn, album_id, user)
    edits.move_spread(conn, album_id, spread_id, direction)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/{album_id}/spreads/{spread_id}/hero/{photo_id}")
def set_hero(
    album_id: int,
    spread_id: int,
    photo_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _require_owned_album(conn, album_id, user)
    edits.set_hero(conn, album_id, spread_id, photo_id)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/{album_id}/spreads/{spread_id}/photos/{photo_id}/move/{direction}")
def move_photo(
    album_id: int,
    spread_id: int,
    photo_id: int,
    direction: str,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    _require_owned_album(conn, album_id, user)
    edits.move_photo(conn, album_id, spread_id, photo_id, direction)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.get("/albums/{album_id}/pdf")
def album_pdf(
    album_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _require_owned_album(conn, album_id, user)
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
def photo_file(
    photo_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    photo = albums.get_photo(conn, photo_id, user["id"])
    if photo is None or not Path(photo["path"]).is_file():
        raise HTTPException(status_code=404, detail="no such photo")
    return FileResponse(photo["path"])
