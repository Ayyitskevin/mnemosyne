#!/usr/bin/env bash
# Run one gallery through grok vision + arrange and print COGS (the Gate-3 number).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GALLERY="${1:-}"
THEME="${MNEMOSYNE_BENCH_THEME:-food}"
OWNER_EMAIL="${MNEMOSYNE_BENCH_OWNER:-}"

if [[ -z "$GALLERY" || ! -d "$GALLERY" ]]; then
  echo "Usage: XAI_API_KEY=... bash scripts/run-cogs-benchmark.sh /path/to/gallery" >&2
  echo "  Optional: MNEMOSYNE_BENCH_THEME=wedding MNEMOSYNE_BENCH_OWNER=you@example.com" >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${XAI_API_KEY:-}" ]]; then
  echo "ERROR: XAI_API_KEY required for grok COGS benchmark" >&2
  exit 1
fi

export MNEMOSYNE_VISION_BACKEND=grok
export MNEMOSYNE_ARRANGE_BACKEND=grok

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

ARGS=(build "$GALLERY" --theme "$THEME")
if [[ -n "$OWNER_EMAIL" ]]; then
  ARGS+=(--owner-email "$OWNER_EMAIL")
fi

echo "==> Building with grok backends (theme=$THEME)"
python -m mnemosyne "${ARGS[@]}"

ALBUM_ID="$(python - <<'PY'
import sqlite3
from mnemosyne import config, db
db.migrate(conn := db.connect(config.DB_PATH))
row = conn.execute("SELECT id FROM albums ORDER BY id DESC LIMIT 1").fetchone()
print(row["id"] if row else "")
PY
)"

if [[ -z "$ALBUM_ID" ]]; then
  echo "ERROR: could not read album id from DB" >&2
  exit 1
fi

echo ""
echo "==> COGS report for album #$ALBUM_ID"
python -m mnemosyne cost "$ALBUM_ID"