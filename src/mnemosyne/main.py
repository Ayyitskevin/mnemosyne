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

import hashlib
import os
import secrets
import tempfile
from io import BytesIO

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.exception_handlers import http_exception_handler
from fastapi.templating import Jinja2Templates
from PIL import Image, UnidentifiedImageError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from mnemosyne import (
    albums,
    auth,
    config,
    db,
    edits,
    export,
    ingest,
    pipeline,
    storage,
    waitlist,
)
from mnemosyne.worker import AlbumWorker

TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "templates")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    conn.close()
    # Start the background album worker. It recovers any album left mid-build by a
    # previous shutdown, then drains 'pending' uploads. Held on app.state so the
    # upload route can wake it the instant a new album is enqueued.
    worker = AlbumWorker(config.DB_PATH)
    worker.start()
    app.state.worker = worker
    try:
        yield
    finally:
        worker.stop()


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


def _wake_worker(request: Request) -> None:
    """Nudge the background worker to pick up a just-enqueued album immediately.
    No-op if there's no worker on app.state (e.g. tests that don't run lifespan) —
    the album stays 'pending' and would be drained by a real worker's poll."""
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.notify()


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


def _is_safe_image(data: bytes) -> bool:
    """Validate the upload body, not just the filename. Pillow verifies the file
    structure, then a second open reads dimensions because verify() consumes the
    decoder state. Pixel caps keep hostile or accidental decompression bombs out
    of the worker queue."""
    try:
        with Image.open(BytesIO(data)) as im:
            im.verify()
        with Image.open(BytesIO(data)) as im:
            width, height = im.size
    except (OSError, UnidentifiedImageError, ValueError):
        return False
    return width > 0 and height > 0 and width * height <= config.MAX_UPLOAD_PIXELS


def _save_uploads(files: list[UploadFile], owner_id: int) -> tuple[Path, int]:
    """Write verified images into a fresh per-album folder and return it plus the
    count saved. Known suffix, byte cap, real-image validation, pixel cap,
    basename-only paths, duplicate-content skips, collision suffixes, and temp
    writes all happen before the album job is enqueued."""
    dest_dir = config.UPLOAD_DIR / f"u{owner_id}_{secrets.token_hex(6)}"
    saved = 0
    seen_hashes: set[bytes] = set()
    for f in files:
        if not f.filename:
            continue
        name = Path(f.filename).name  # strip any directory components
        if Path(name).suffix.lower() not in ingest.IMAGE_SUFFIXES:
            continue
        data = f.file.read(config.MAX_UPLOAD_FILE_BYTES + 1)
        if not data or len(data) > config.MAX_UPLOAD_FILE_BYTES:
            continue
        digest = hashlib.sha256(data).digest()
        if digest in seen_hashes or not _is_safe_image(data):
            continue
        seen_hashes.add(digest)

        dest_dir.mkdir(parents=True, exist_ok=True)  # only once we have a keeper
        target = dest_dir / name
        n = 1
        while target.exists():
            target = dest_dir / f"{target.stem}_{n}{target.suffix}"
            n += 1
        tmp = dest_dir / f".{secrets.token_hex(8)}.upload"
        tmp.write_bytes(data)
        tmp.replace(target)
        saved += 1
    return dest_dir, saved


@app.get("/albums/new", response_class=HTMLResponse)
def new_album_form(request: Request, user: dict = Depends(require_user)):
    return TEMPLATES.TemplateResponse(
        request, "new_album.html", {"user": user, "max_photos": config.MAX_ALBUM_UPLOAD}
    )


@app.post("/albums/new", response_class=HTMLResponse)
def create_album(
    request: Request,
    name: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Create an album from a browser upload: save verified photos, enqueue the
    background album job, then redirect to the status page. Validation lives here,
    at the public boundary, before any upload becomes worker input."""
    real = [f for f in photos if f.filename]
    if len(real) > config.MAX_ALBUM_UPLOAD:
        return TEMPLATES.TemplateResponse(
            request,
            "new_album.html",
            {
                "user": user,
                "max_photos": config.MAX_ALBUM_UPLOAD,
                "name": name,
                "error": f"Too many photos — upload at most {config.MAX_ALBUM_UPLOAD} at once.",
            },
        )

    dest_dir, saved = _save_uploads(real, user["id"])
    if saved == 0:
        return TEMPLATES.TemplateResponse(
            request,
            "new_album.html",
            {
                "user": user,
                "max_photos": config.MAX_ALBUM_UPLOAD,
                "name": name,
                "error": "No usable images found — add some JPG or PNG photos.",
            },
        )

    album_name = name.strip() or "Untitled album"
    album_id = pipeline.enqueue_album(
        conn, name=album_name, source_dir=dest_dir, owner_id=user["id"]
    )
    _wake_worker(request)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.get("/albums/{album_id}", response_class=HTMLResponse)
def show_album(
    album_id: int,
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    album = albums.get_album(conn, album_id, user["id"])
    if album is None:
        raise HTTPException(status_code=404, detail="no such album")
    # A still-building or failed album has no spreads to render yet — show its
    # status page (which auto-refreshes while processing) instead of the layout.
    if album["status"] != "ready":
        return TEMPLATES.TemplateResponse(
            request, "album_status.html", {"album": album, "user": user}
        )
    data = albums.album_for_render(conn, album_id, user["id"])
    return TEMPLATES.TemplateResponse(
        request,
        "album.html",
        {"album": data["album"], "spreads": data["spreads"], "user": user},
    )


@app.post("/albums/{album_id}/retry")
def retry_album(
    album_id: int,
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Re-queue a failed album (owner only). requeue_album no-ops unless it's
    actually 'failed', so this can't disturb a ready or in-flight one."""
    _require_owned_album(conn, album_id, user)
    if pipeline.requeue_album(conn, album_id):
        _wake_worker(request)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.get("/albums/{album_id}/delete", response_class=HTMLResponse)
def delete_album_form(
    album_id: int,
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Confirm page for a destructive delete (owner only). A GET never mutates —
    it just shows what's about to be removed and a POST button to go through with
    it. 404s on a non-owned id like every other album route."""
    _require_owned_album(conn, album_id, user)
    album = albums.get_album(conn, album_id, user["id"])
    if album["status"] in ("pending", "processing"):
        return RedirectResponse(f"/albums/{album_id}", status_code=303)
    return TEMPLATES.TemplateResponse(
        request, "delete_confirm.html", {"album": album, "user": user}
    )


@app.post("/albums/{album_id}/delete")
def delete_album(
    album_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Permanently delete an album and its photos/spreads/files (owner only).
    Irreversible, so it's reached only via POST from the confirm page."""
    _require_owned_album(conn, album_id, user)
    if pipeline.delete_album(conn, album_id):
        return RedirectResponse("/albums", status_code=303)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


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
    store = storage.get_storage()
    if photo is None or not store.exists(photo["path"]):
        raise HTTPException(status_code=404, detail="no such photo")
    # If the backend can hand the browser a direct URL (object store), redirect to
    # it so the bytes never proxy through this process; otherwise (local disk)
    # stream the file ourselves.
    url = store.signed_url(photo["path"])
    if url is not None:
        return RedirectResponse(url)
    return FileResponse(store.local_path(photo["path"]))
