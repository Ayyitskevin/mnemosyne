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
import logging
import os
import secrets
import tempfile
from io import BytesIO
from urllib.parse import quote_plus

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
    billing,
    config,
    db,
    edits,
    export,
    ingest,
    mise_client,
    mise_import,
    pipeline,
    plutus_api,
    plutus_link,
    runtime,
    share,
    themes,
    storage,
    urls,
    usage,
    waitlist,
)

log = logging.getLogger("mnemosyne.main")
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
def healthz(request: Request) -> dict:
    worker = getattr(request.app.state, "worker", None)
    summary = runtime.health_summary()
    return {
        "ok": summary["status"] == "ok",
        "status": summary["status"],
        "worker": worker is not None,
        "backends": summary["backends"],
        "storage": summary["storage"],
    }


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """The public pitch. No data of its own — its only job is to make the promise
    and capture an email, so it needs no DB read."""
    return TEMPLATES.TemplateResponse(request, "landing.html", {})


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy(request: Request):
    """Trust surface — how we handle galleries and the no-training promise."""
    return TEMPLATES.TemplateResponse(request, "privacy.html", {})


@app.get("/terms", response_class=HTMLResponse)
def terms_of_service(request: Request):
    """Trust surface — service terms (draft until billing ships)."""
    return TEMPLATES.TemplateResponse(request, "terms.html", {})


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


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request):
    return TEMPLATES.TemplateResponse(request, "forgot_password.html", {})


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password(
    request: Request,
    email: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    raw = auth.request_password_reset(conn, email)
    dev_link = None
    if raw and config.DEV_RESET_LINKS:
        base = urls.public_base(request)
        dev_link = f"{base}/reset-password?token={raw}"
        log.info("password reset link for %s: %s", email, dev_link)
    return TEMPLATES.TemplateResponse(
        request,
        "forgot_password.html",
        {"sent": True, "dev_link": dev_link},
    )


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_form(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/forgot-password", status_code=303)
    return TEMPLATES.TemplateResponse(
        request, "reset_password.html", {"token": token}
    )


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not auth.reset_password(conn, token, password):
        return TEMPLATES.TemplateResponse(
            request,
            "reset_password.html",
            {"token": token, "error": "Invalid or expired reset link."},
        )
    return RedirectResponse("/login", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: dict = Depends(require_user)):
    return TEMPLATES.TemplateResponse(
        request, "account.html", {"user": user}
    )


@app.post("/account/delete")
def delete_account(
    request: Request,
    password: str = Form(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = auth.get_user_by_id(conn, user["id"])
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {"user": user, "error": "Password incorrect."},
        )
    if not auth.delete_user(conn, user["id"], pipeline.delete_album):
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {
                "user": user,
                "error": "Could not delete — wait for in-flight albums to finish.",
            },
        )
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, user: dict = Depends(require_user)):
    return TEMPLATES.TemplateResponse(
        request,
        "billing.html",
        {"user": user, "billing": billing.billing_view(user)},
    )


@app.post("/billing/checkout")
def billing_checkout(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        session = billing.create_checkout_session(conn, user)
    except billing.BillingError as exc:
        return RedirectResponse(
            f"/billing?error={quote_plus(str(exc)[:80])}", status_code=303
        )
    return RedirectResponse(session["checkout_url"], status_code=303)


@app.post("/billing/portal")
def billing_portal(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        portal = billing.create_portal_session(conn, user)
    except billing.BillingError as exc:
        return RedirectResponse(
            f"/billing?error={quote_plus(str(exc)[:80])}", status_code=303
        )
    return RedirectResponse(portal["portal_url"], status_code=303)


@app.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    if not billing.billing_enabled():
        raise HTTPException(status_code=404, detail="billing disabled")
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not billing.verify_webhook_signature(payload, sig):
        raise HTTPException(status_code=400, detail="bad signature")
    billing.handle_webhook(conn, payload)
    return {"ok": True}


@app.get("/albums", response_class=HTMLResponse)
def index(
    request: Request,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = albums.list_albums(conn, user["id"])
    cogs = usage.summaries_for_albums(conn, [a["id"] for a in rows])
    for row in rows:
        row["cogs"] = cogs.get(row["id"])
    return TEMPLATES.TemplateResponse(
        request,
        "albums.html",
        {"albums": rows, "user": user, "runtime": runtime.backend_status()},
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
        request,
        "new_album.html",
        {
            "user": user,
            "max_photos": config.MAX_ALBUM_UPLOAD,
            "themes": themes.THEMES,
            "mise_import_enabled": mise_client.configured(),
        },
    )


@app.get("/albums/import/mise", response_class=HTMLResponse)
def import_mise_form(request: Request, user: dict = Depends(require_user)):
    if not mise_client.configured():
        raise HTTPException(status_code=404, detail="mise import not configured")
    galleries: list[dict] = []
    error = None
    try:
        body = mise_client.list_galleries(published=True)
        galleries = body.get("galleries") or []
    except mise_client.MiseClientError as exc:
        error = str(exc)
    return TEMPLATES.TemplateResponse(
        request,
        "import_mise.html",
        {
            "user": user,
            "galleries": galleries,
            "themes": themes.THEMES,
            "error": error,
        },
    )


@app.post("/albums/import/mise")
def import_mise_gallery(
    request: Request,
    gallery_id: int = Form(...),
    gallery_theme: str = Form("food"),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not mise_client.configured():
        raise HTTPException(status_code=404, detail="mise import not configured")
    if not billing.upload_allowed(user):
        return RedirectResponse("/billing", status_code=303)
    try:
        album_id = mise_import.import_gallery(
            conn,
            owner_id=user["id"],
            gallery_id=gallery_id,
            gallery_theme=themes.normalize_theme(gallery_theme),
        )
    except mise_import.MiseImportError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "import_mise.html",
            {
                "user": user,
                "galleries": [],
                "themes": themes.THEMES,
                "error": str(exc),
                "gallery_id": gallery_id,
                "gallery_theme": gallery_theme,
            },
            status_code=400,
        )
    _wake_worker(request)
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/new", response_class=HTMLResponse)
def create_album(
    request: Request,
    name: str = Form(""),
    gallery_theme: str = Form("food"),
    photos: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Create an album from a browser upload: save verified photos, enqueue the
    background album job, then redirect to the status page. Validation lives here,
    at the public boundary, before any upload becomes worker input."""
    if not billing.upload_allowed(user):
        return RedirectResponse("/billing", status_code=303)
    real = [f for f in photos if f.filename]
    if len(real) > config.MAX_ALBUM_UPLOAD:
        return TEMPLATES.TemplateResponse(
            request,
            "new_album.html",
            {
                "user": user,
                "max_photos": config.MAX_ALBUM_UPLOAD,
                "name": name,
                "gallery_theme": gallery_theme,
                "themes": themes.THEMES,
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
                "gallery_theme": gallery_theme,
                "themes": themes.THEMES,
                "error": "No usable images found — add some JPG or PNG photos.",
            },
        )

    album_name = name.strip() or "Untitled album"
    album_id = pipeline.enqueue_album(
        conn,
        name=album_name,
        source_dir=dest_dir,
        owner_id=user["id"],
        gallery_theme=themes.normalize_theme(gallery_theme),
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
    # Hand the template a ready-to-copy absolute link when one is live, so the owner
    # can paste it to a client without us hardcoding the host.
    share_link = None
    if data["album"].get("share_token"):
        share_link = urls.share_url(request, data["album"]["share_token"])
    cost_report = usage.album_cost_report(conn, album_id)
    photo_count = conn.execute(
        "SELECT COUNT(*) AS n FROM photos WHERE album_id = ?", (album_id,)
    ).fetchone()["n"]
    return TEMPLATES.TemplateResponse(
        request,
        "album.html",
        {
            "album": data["album"],
            "spreads": data["spreads"],
            "user": user,
            "share_url": share_link,
            "cost_report": cost_report,
            "cost_label": usage.format_cost(cost_report.get("cost_usd")),
            "photo_count": photo_count,
            "spread_count": len(data["spreads"]),
            "regenerated": request.query_params.get("regenerated") == "1",
            "regenerate_error": request.query_params.get("regenerate_error"),
            "plutus_saved": request.query_params.get("plutus_saved") == "1",
            "plutus_error": request.query_params.get("plutus_error"),
            "plutus_configured": bool(config.PLUTUS_URL),
            "plutus_api_configured": plutus_api.configured(),
        },
    )


@app.post("/albums/{album_id}/regenerate")
def regenerate_album(
    album_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Re-run arrange only — keeps vision, replaces spreads (owner confirm in UI)."""
    _require_owned_album(conn, album_id, user)
    try:
        pipeline.regenerate_layout(conn, album_id)
    except LookupError:
        return RedirectResponse(
            f"/albums/{album_id}?regenerate_error=album+not+ready",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            f"/albums/{album_id}?regenerate_error={quote_plus(str(exc)[:120])}",
            status_code=303,
        )
    return RedirectResponse(f"/albums/{album_id}?regenerated=1", status_code=303)


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


def _serve_key(key: str):
    """Hand the browser the bytes behind a storage key. If the backend can mint a
    direct URL (object store), redirect so the bytes never proxy through this
    process; otherwise (local disk) stream the file. 404s a key with no object."""
    store = storage.get_storage()
    if not store.exists(key):
        raise HTTPException(status_code=404, detail="no such photo")
    url = store.signed_url(key)
    if url is not None:
        return RedirectResponse(url)
    return FileResponse(store.local_path(key))


@app.get("/photo/{photo_id}")
def photo_file(
    photo_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    photo = albums.get_photo(conn, photo_id, user["id"])
    if photo is None:
        raise HTTPException(status_code=404, detail="no such photo")
    return _serve_key(photo["storage_key"])


# --- share links: an owner hands a finished album to someone with no account -----


@app.post("/albums/{album_id}/plutus-generate")
def generate_plutus_offer(
    album_id: int,
    plutus_run_id: int = Form(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Mint a Plutus storefront offer from a bundles run id via API."""
    _require_owned_album(conn, album_id, user)
    try:
        offer = plutus_api.create_offer_url(
            run_id=plutus_run_id,
            label=conn.execute(
                "SELECT name FROM albums WHERE id = ?", (album_id,)
            ).fetchone()["name"],
        )
        plutus_link.save_offer_url(conn, album_id, user["id"], offer)
    except (plutus_api.PlutusApiError, TypeError, ValueError) as exc:
        return RedirectResponse(
            f"/albums/{album_id}?plutus_error={quote_plus(str(exc)[:120])}",
            status_code=303,
        )
    return RedirectResponse(f"/albums/{album_id}?plutus_saved=1", status_code=303)


@app.post("/albums/{album_id}/plutus-link")
def save_plutus_link(
    album_id: int,
    plutus_offer_url: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _require_owned_album(conn, album_id, user)
    saved = plutus_link.save_offer_url(conn, album_id, user["id"], plutus_offer_url)
    if plutus_offer_url.strip() and saved is None:
        return RedirectResponse(
            f"/albums/{album_id}?plutus_error=invalid+Plutus+offer+URL",
            status_code=303,
        )
    return RedirectResponse(f"/albums/{album_id}?plutus_saved=1", status_code=303)


@app.post("/albums/{album_id}/share")
def create_share(
    album_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Mint (or rotate) a time-limited public link for an owned album. Re-minting
    rotates the token, invalidating any link already shared — the 'start over'
    affordance. Owner-gated like every other album write."""
    _require_owned_album(conn, album_id, user)
    share.create_link(conn, album_id, user["id"])
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.post("/albums/{album_id}/share/revoke")
def revoke_share(
    album_id: int,
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Kill an owned album's share link immediately, before its expiry."""
    _require_owned_album(conn, album_id, user)
    share.revoke_link(conn, album_id, user["id"])
    return RedirectResponse(f"/albums/{album_id}", status_code=303)


@app.get("/share/{token}", response_class=HTMLResponse)
def shared_album(
    token: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Public read-only view of a shared album — no session required, the token is
    the authorization. 404s an unknown/expired/revoked token (and a not-yet-ready
    album) without revealing whether the token ever existed. No edit or delete
    controls render here; this is the client's window onto the finished book."""
    album_id = share.resolve_token(conn, token)
    if album_id is None:
        raise HTTPException(status_code=404, detail="link not found")
    data = albums.album_for_render(conn, album_id)
    return TEMPLATES.TemplateResponse(
        request,
        "share.html",
        {
            "album": data["album"],
            "spreads": data["spreads"],
            "token": token,
            "plutus_offer_url": data["album"].get("plutus_offer_url"),
            "plutus_cta": plutus_link.offer_cta_label(),
        },
    )


@app.get("/share/{token}/photo/{photo_id}")
def shared_photo(
    token: str,
    photo_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Image bytes for the shared view, scoped to the token's own album so a valid
    token can't be paired with a guessed photo_id from another gallery."""
    key = share.shared_photo_key(conn, token, photo_id)
    if key is None:
        raise HTTPException(status_code=404, detail="no such photo")
    return _serve_key(key)


@app.get("/share/{token}/pdf")
def shared_pdf(
    token: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """The same print-ready PDF the owner gets, downloadable by a link holder."""
    album_id = share.resolve_token(conn, token)
    if album_id is None:
        raise HTTPException(status_code=404, detail="link not found")
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        export.export_album(conn, album_id, tmp)
        pdf = Path(tmp).read_bytes()
    finally:
        Path(tmp).unlink(missing_ok=True)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="album-{album_id}.pdf"'},
    )
