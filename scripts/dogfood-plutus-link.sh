#!/usr/bin/env bash
# Prod dogfood: mint Plutus offer via API and attach to a ready mnemosyne album.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

ALBUM_ID="${1:-${MNEMOSYNE_ALBUM_ID:-}}"
RUN_ID="${2:-${PLUTUS_RUN_ID:-}}"
EMAIL="${MNEMOSYNE_DOGFOOD_EMAIL:-}"
PASSWORD="${MNEMOSYNE_DOGFOOD_PASSWORD:-}"
BASE="${MNEMOSYNE_URL:-http://127.0.0.1:${MNEMOSYNE_PORT:-8000}}"
BASE="${BASE%/}"

if [[ -z "$ALBUM_ID" || -z "$RUN_ID" ]]; then
  echo "usage: MNEMOSYNE_DOGFOOD_EMAIL=… MNEMOSYNE_DOGFOOD_PASSWORD=… \\" >&2
  echo "       bash scripts/dogfood-plutus-link.sh <album_id> <plutus_run_id>" >&2
  echo "" >&2
  echo "Requires MNEMOSYNE_PLUTUS_URL, MNEMOSYNE_PLUTUS_API_TOKEN, MNEMOSYNE_PLUTUS_TENANT_ID" >&2
  exit 1
fi
if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
  echo "Set MNEMOSYNE_DOGFOOD_EMAIL and MNEMOSYNE_DOGFOOD_PASSWORD" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true
PYTHON="${PYTHON:-python3}"

echo "==> Plutus API configured?"
"$PYTHON" - <<PY
import sys
sys.path.insert(0, "${ROOT}/src")
from mnemosyne import plutus_api
assert plutus_api.configured(), (
    "wire MNEMOSYNE_PLUTUS_URL, MNEMOSYNE_PLUTUS_API_TOKEN, MNEMOSYNE_PLUTUS_TENANT_ID"
)
print("  plutus_api OK")
PY

echo "==> Login + plutus-generate (album=${ALBUM_ID}, run=${RUN_ID})"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

curl -sf -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST "$BASE/login" \
  -d "email=${EMAIL}&password=${PASSWORD}" -o /dev/null -w "%{http_code}" | grep -q 303

LOC=$(curl -sf -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST \
  "$BASE/albums/${ALBUM_ID}/plutus-generate" \
  -d "plutus_run_id=${RUN_ID}" -o /dev/null -w "%{redirect_url}")
if [[ "$LOC" != *"plutus_saved=1"* && "$LOC" != *"plutus_error"* ]]; then
  echo "unexpected redirect: $LOC" >&2
  exit 1
fi
if [[ "$LOC" == *"plutus_error"* ]]; then
  echo "plutus-generate failed: $LOC" >&2
  exit 1
fi
echo "  offer saved on album"

curl -sf -c "$COOKIE_JAR" -b "$COOKIE_JAR" -X POST \
  "$BASE/albums/${ALBUM_ID}/share" -o /dev/null

if [[ -n "${MNEMOSYNE_DB:-}" && -f "$MNEMOSYNE_DB" ]]; then
  SHARE_INFO=$("$PYTHON" - <<PY
import sqlite3, os
row = sqlite3.connect(os.environ["MNEMOSYNE_DB"]).execute(
    "SELECT share_token, plutus_offer_url FROM albums WHERE id = ?",
    (int("${ALBUM_ID}"),),
).fetchone()
print(row[0] or "", row[1] or "", sep="|")
PY
)
  TOKEN="${SHARE_INFO%%|*}"
  OFFER="${SHARE_INFO#*|}"
  echo "  share token: ${TOKEN}"
  echo "  offer URL: ${OFFER}"
  if [[ -n "$TOKEN" ]]; then
    curl -sf "$BASE/share/${TOKEN}" | grep -q "Order prints" && echo "  share CTA OK"
  fi
fi

echo "==> Mnemosyne Plutus link dogfood OK"