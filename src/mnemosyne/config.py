"""Application configuration, read from the environment.

Keeping config in one place (and env-driven) means the same code runs here on
mickey and anywhere else without edits — you just point the env vars elsewhere.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env (gitignored, per-machine) before reading any env var below, so secrets
# like XAI_API_KEY live in a file off the repo rather than the shell history.
load_dotenv()

# The SQLite file mnemosyne stores everything in. Override with MNEMOSYNE_DB.
DB_PATH = Path(os.environ.get("MNEMOSYNE_DB", "mnemosyne.db"))

# Canonical public origin for client-facing links (share URLs). When mnemosyne
# sits behind Cloudflare or a tunnel, set this to the hostname clients use —
# otherwise share links fall back to request.base_url (fine for local dogfood).
PUBLIC_URL = os.environ.get("MNEMOSYNE_PUBLIC_URL") or None

# Optional Plutus SaaS base for print cross-sell (e.g. https://plutus.kleephotography.com).
# Albums can store a full offer URL or a /store/{slug}/offer/{token} path.
PLUTUS_URL = os.environ.get("MNEMOSYNE_PLUTUS_URL") or None
PLUTUS_API_TOKEN = os.environ.get("MNEMOSYNE_PLUTUS_API_TOKEN") or None
# SaaS tenant id when minting offers via admin API token (e.g. flow-studio).
PLUTUS_TENANT_ID = os.environ.get("MNEMOSYNE_PLUTUS_TENANT_ID") or None
# When true, worker mints an offer via API after album reaches ready (needs run id).
PLUTUS_AUTO_LINK = os.environ.get("MNEMOSYNE_PLUTUS_AUTO_LINK", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
# Fallback bundles run id when album.plutus_run_id is unset (dogfood / single-tenant).
PLUTUS_DEFAULT_RUN_ID = os.environ.get("MNEMOSYNE_PLUTUS_RUN_ID") or None

# Mise gallery import — read-only GET /api/galleries (same token as Argus on flow).
MISE_URL = os.environ.get("MNEMOSYNE_MISE_URL") or os.environ.get("MISE_URL") or None
MISE_API_TOKEN = (
    os.environ.get("MNEMOSYNE_MISE_API_TOKEN")
    or os.environ.get("MISE_ARGUS_TOKEN")
    or os.environ.get("ARGUS_MISE_API_TOKEN")
    or None
)
MISE_TIMEOUT = float(os.environ.get("MNEMOSYNE_MISE_TIMEOUT", "30"))
# Per-asset signal endpoint, relative to MISE_URL, with a {gallery_id} placeholder.
# This is the ONE knob to retarget if Mise's real route/field names differ from the
# assumed default — mise_client.list_assets is tolerant about the response shape, so
# only the path is environment-driven. When the endpoint is absent or errors, the
# import falls back to local vision (no signal read), so this can't break a build.
MISE_ASSETS_PATH = os.environ.get(
    "MNEMOSYNE_MISE_ASSETS_PATH", "/api/galleries/{gallery_id}/assets"
)
# Local mirror of Mise originals (e.g. after sync-mise-media.sh) — checked before API path.
MISE_MEDIA_ROOT = (
    Path(os.environ["MNEMOSYNE_MISE_MEDIA_ROOT"])
    if os.environ.get("MNEMOSYNE_MISE_MEDIA_ROOT")
    else None
)

# Stripe billing — off until STRIPE_ENABLED=true and keys are set (commitment-class).
STRIPE_ENABLED = os.environ.get("MNEMOSYNE_STRIPE_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY") or os.environ.get(
    "MNEMOSYNE_STRIPE_SECRET_KEY"
)
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET") or os.environ.get(
    "MNEMOSYNE_STRIPE_WEBHOOK_SECRET"
)
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID") or os.environ.get(
    "MNEMOSYNE_STRIPE_PRICE_ID"
)
_public = (PUBLIC_URL or "http://localhost:8000").rstrip("/")
STRIPE_SUCCESS_URL = os.environ.get("MNEMOSYNE_STRIPE_SUCCESS_URL") or f"{_public}/billing?success=1"
STRIPE_CANCEL_URL = os.environ.get("MNEMOSYNE_STRIPE_CANCEL_URL") or f"{_public}/billing?canceled=1"
STRIPE_PORTAL_RETURN_URL = os.environ.get(
    "MNEMOSYNE_STRIPE_PORTAL_RETURN_URL"
) or f"{_public}/billing"

# Password reset — log reset links when unset (dogfood only).
SMTP_URL = os.environ.get("MNEMOSYNE_SMTP_URL") or None
DEV_RESET_LINKS = os.environ.get("MNEMOSYNE_DEV_RESET_LINKS", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
RESET_TOKEN_TTL_HOURS = int(os.environ.get("MNEMOSYNE_RESET_TOKEN_TTL_HOURS") or 24)

# Signing key for the session cookie (Starlette SessionMiddleware). A logged-in
# user's id rides in a cookie signed with this; rotating it logs everyone out.
# In prod set MNEMOSYNE_SECRET_KEY to a fixed random value (in .env, off the repo)
# so restarts don't drop sessions — the dev fallback is a fresh key per boot.
SECRET_KEY = os.environ.get("MNEMOSYNE_SECRET_KEY") or os.urandom(32).hex()

# The root the local storage driver writes photo bytes under, one `a<album_id>/`
# subfolder per album. Ingest `put`s each photo here under a relative storage key
# and /photo/<id> serves it back through the seam, so this is permanent storage,
# not scratch — gitignored, per-machine. (With STORAGE_BACKEND=r2 the same keys
# live in a bucket and this path is unused.)
UPLOAD_DIR = Path(os.environ.get("MNEMOSYNE_UPLOAD_DIR", "uploads"))

# Which storage driver backs photo bytes (see storage.py). "local" = this box's
# filesystem (the default). "r2" = Cloudflare R2 object storage — the dormant
# driver that lets mnemosyne run off a single box. Flipping this var is the whole
# swap; ingest/vision/export/web never change because they speak only the seam.
STORAGE_BACKEND = os.environ.get("MNEMOSYNE_STORAGE_BACKEND", "local")

# Retire-readiness / no-media-duplication: when true (and on local storage), a
# Mise-imported album REFERENCES the gallery's originals in place instead of copying
# their bytes into mnemosyne's own store — Mise owns the media, mnemosyne keeps only
# a layout cache. The photo's storage_key becomes the original's absolute path, which
# the local driver serves as a passthrough; deletion is containment-guarded so the
# referenced originals are never removed. OPT-IN (default off) so the existing copy
# behavior is unchanged; only applies to Mise imports on local storage (uploads land
# in an ephemeral staging dir, and R2 can't reference a local path, so both still copy).
REFERENCE_MISE_ORIGINALS = os.environ.get(
    "MNEMOSYNE_REFERENCE_MISE_ORIGINALS", ""
).strip().lower() in {"1", "true", "yes"}

# Cloudflare R2 (S3-compatible) settings — read only when STORAGE_BACKEND=r2. All
# secrets live in .env (gitignored, per-machine), NEVER in the repo. Unset until a
# real bucket exists; the R2 driver fails loud if selected without these. ENDPOINT
# is the account's S3 API URL (https://<acct>.r2.cloudflarestorage.com). PUBLIC_
# BASE_URL, when set (an r2.dev or custom-domain bucket), makes /photo redirect to
# a plain public URL instead of minting a presigned one each request.
R2_ENDPOINT = os.environ.get("MNEMOSYNE_R2_ENDPOINT")
R2_BUCKET = os.environ.get("MNEMOSYNE_R2_BUCKET")
R2_ACCESS_KEY_ID = os.environ.get("MNEMOSYNE_R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("MNEMOSYNE_R2_SECRET_ACCESS_KEY")
R2_PUBLIC_BASE_URL = os.environ.get("MNEMOSYNE_R2_PUBLIC_BASE_URL")
R2_SIGNED_URL_TTL = int(os.environ.get("MNEMOSYNE_R2_SIGNED_URL_TTL", "3600"))

# Cap on photos per web upload. The vision pipeline now runs in the background
# worker, not in the request, so this is no longer about request timeouts — it's
# a sanity bound on one submit (disk + a single album's queue time), sized for a
# real wedding/event gallery. The CLI `build` path has no cap (local operator,
# not a web stranger).
MAX_ALBUM_UPLOAD = int(os.environ.get("MNEMOSYNE_MAX_ALBUM_UPLOAD", "500"))

# Per-file and pixel-count upload caps. A file may have a safe-looking suffix but
# still be a decompression bomb or a huge payload; validate before anything is
# committed into an album job.
MAX_UPLOAD_FILE_BYTES = int(
    os.environ.get("MNEMOSYNE_MAX_UPLOAD_FILE_BYTES", str(50 * 1024 * 1024))
)
MAX_UPLOAD_PIXELS = int(
    os.environ.get("MNEMOSYNE_MAX_UPLOAD_PIXELS", str(100_000_000))
)

# How long (seconds) a worker's claim on a 'processing' album is trusted before
# another worker may reclaim it. It guards crash recovery in a multi-process
# setup: a job whose lease has gone stale is assumed to belong to a dead worker
# and is re-queued. There is no heartbeat, so this must comfortably exceed the
# longest real build — a job that outruns the lease can be re-run by a sibling,
# which is wasteful but safe because process_album is idempotent.
WORKER_LEASE_SECONDS = int(os.environ.get("MNEMOSYNE_WORKER_LEASE_SECONDS", "900"))

# The local Ollama fleet on mickey. Phase 0 runs ENTIRELY on-box — images are
# analyzed here and never leave the machine, so "we don't train on your images"
# is true by construction. (When mnemosyne becomes a hosted SaaS, these point at
# cloud inference and that cost gets priced into the subscription.)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# qwen3-vl:32b is mickey's clean/automation vision lane; qwen3.6:35b is a mid
# reasoning model for the arrange step. Both overridable via env.
VISION_MODEL = os.environ.get("MNEMOSYNE_VISION_MODEL", "qwen3-vl:32b")
ARRANGE_MODEL = os.environ.get("MNEMOSYNE_ARRANGE_MODEL", "qwen3.6:35b")

# Phase 3: optional delegation of vision "look" step to argus service (over tailnet or local).
# When set (e.g. "http://mickey:8010" or "http://127.0.0.1:8010"), mnemosyne vision will
# call argus /analyze (path must be visible to argus server) and map results to {scene, hero_score}.
# This keeps mnemosyne from loading heavy vision models directly during integration dev.
# Force argus side with ARGUS_VISION_BACKEND=mock.
ARGUS_URL = os.environ.get("ARGUS_URL") or os.environ.get("MNEMOSYNE_ARGUS_URL")
ARGUS_API_TOKEN = os.environ.get("ARGUS_API_TOKEN") or os.environ.get("MNEMOSYNE_ARGUS_API_TOKEN")
# path = argus reads image_path on its host (homelab shared disk).
# upload = multipart file POST (decoupled hosts / Argus SaaS upload-only).
ARGUS_DELEGATION_MODE = os.environ.get("MNEMOSYNE_ARGUS_DELEGATION_MODE", "path").strip().lower()

# Phase 2 runtime vision vendor: xAI / Grok (OpenAI-compatible API). This is the
# SaaS production lane — a metered API bill, separate from any coding subscription.
# Local qwen3-vl stays the dev A/B oracle + dogfood path, NOT prod failover.
XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
# grok-2-vision was retired May 2026; the current lineup (grok-4.3 / grok-4.20) is
# all multimodal. For a per-photo scene+hero classify (judgment, not reasoning —
# CLAUDE.md Rule 5) the non-reasoning 4.20 variant is the cheapest right fit. The
# id was verified to resolve against the live API 2026-06-23 (403-no-credits, not
# 404), so it's real; a billed call still needs credits loaded on the xAI team.
GROK_VISION_MODEL = os.environ.get("MNEMOSYNE_GROK_VISION_MODEL", "grok-4.20-non-reasoning")

# Explicit per-photo vision backend selector: "grok" | "argus" | "ollama".
# Unset = local-first default (argus if ARGUS_URL set, else ollama) — keeps the
# existing dogfood path unsurprised. Flipping this one var is the whole A/B.
VISION_BACKEND = os.environ.get("MNEMOSYNE_VISION_BACKEND")

# The arrange (reasoning) step's backend, mirroring the vision selector: "grok"
# routes the layout call to xAI's cloud API; "deterministic" lays the album out with
# the in-repo curate engine (arc-sequenced + paced from the stored hero/keeper
# signals, no model, fully reproducible); unset/anything else stays on local Ollama
# (config.ARRANGE_MODEL). A real cloud host has no Ollama, so this is how arrange runs
# there — and like vision it is OPT-IN, so the existing path is never silently swapped.
ARRANGE_BACKEND = os.environ.get("MNEMOSYNE_ARRANGE_BACKEND")
# The grok model for the arrange call. Layout is a judgment task (Rule 5), so the
# cheaper non-reasoning variant fits — no reasoning-token bill for a JSON layout.
GROK_ARRANGE_MODEL = os.environ.get("MNEMOSYNE_GROK_ARRANGE_MODEL", "grok-4.20-non-reasoning")

# Price per 1M tokens on the grok cloud lane, used to turn the recorded token
# counts into a $/album COGS figure (see usage.py + the inference_usage table).
# Default 0 = UNPRICED: cost is stored as NULL (honestly "unknown", not "free") and
# the token counts remain the ground truth. Set these from xAI's price sheet in
# .env (off the repo) to make dollars appear; prompt and completion bill separately.
GROK_PRICE_PROMPT_PER_M = float(os.environ.get("MNEMOSYNE_GROK_PRICE_PROMPT_PER_M") or 0)
GROK_PRICE_COMPLETION_PER_M = float(os.environ.get("MNEMOSYNE_GROK_PRICE_COMPLETION_PER_M") or 0)

# How long a freshly minted album share link stays live, in days. Bounds the
# exposure of a forwarded/leaked link without the owner having to remember to
# revoke; the owner can still revoke early. Configurable so bumping the window is a
# .env change, not a migration.
SHARE_LINK_TTL_DAYS = int(os.environ.get("MNEMOSYNE_SHARE_LINK_TTL_DAYS") or 30)

# Where per-photo cloud-vision cost lines (tokens + latency) are appended, so
# $/album is reconstructable from call one. Local + gitignored by default; high
# volume, so deliberately NOT the shared fleet routing log.
ROUTING_LOG = Path(os.environ.get("MNEMOSYNE_ROUTING_LOG", "mnemosyne-vision.log"))
