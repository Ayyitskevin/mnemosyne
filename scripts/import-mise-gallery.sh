#!/usr/bin/env bash
# Import a published Mise gallery into mnemosyne (enqueue album build).
# Requires MNEMOSYNE_MISE_* and synced originals under MNEMOSYNE_MISE_MEDIA_ROOT.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"
GALLERY_ID="${1:-${MISE_GALLERY_ID:-1}}"
THEME="${2:-food}"
OWNER_EMAIL="${MNEMOSYNE_DOGFOOD_EMAIL:-bench@example.com}"
ALLOW_DUP="${MNEMOSYNE_MISE_IMPORT_ALLOW_DUPLICATE:-}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true

MNEMOSYNE_MISE_IMPORT_ALLOW_DUPLICATE="$ALLOW_DUP" python3 - <<PY
import os, sys
sys.path.insert(0, "${ROOT}/src")
from mnemosyne import db, config, mise_import

gallery_id = int("${GALLERY_ID}")
theme = "${THEME}"
owner_email = "${OWNER_EMAIL}"
allow_dup = os.environ.get("MNEMOSYNE_MISE_IMPORT_ALLOW_DUPLICATE", "").lower() in {"1", "true"}

conn = db.connect(config.DB_PATH)
row = conn.execute("SELECT id FROM users WHERE email = ?", (owner_email,)).fetchone()
if row is None:
    raise SystemExit(f"owner not found: {owner_email}")
owner_id = row[0]

try:
    album_id = mise_import.import_gallery(
        conn,
        owner_id=owner_id,
        gallery_id=gallery_id,
        gallery_theme=theme,
        allow_duplicate=allow_dup,
    )
except mise_import.MiseImportError as exc:
    raise SystemExit(str(exc)) from exc

print(f"enqueued album_id={album_id} (mise gallery {gallery_id})")
PY