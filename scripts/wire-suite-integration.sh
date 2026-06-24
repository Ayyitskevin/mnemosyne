#!/usr/bin/env bash
# One-shot wiring for Mise → Plutus → Mnemosyne suite dogfood on homelab.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"
ARGUS_ENV="${ARGUS_ENV_FILE:-$ROOT/../argus/.env}"
PLUTUS_ENV="${PLUTUS_ENV_FILE:-$ROOT/../plutus/.env}"

upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

if [[ -f "$ARGUS_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ARGUS_ENV"
  set +a
fi
if [[ -f "$PLUTUS_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$PLUTUS_ENV"
  set +a
fi

echo "==> Mise import"
MNEMOSYNE_MISE_URL="${MNEMOSYNE_MISE_URL:-${ARGUS_MISE_URL:-http://flow:8400}}" \
MNEMOSYNE_MISE_API_TOKEN="${MNEMOSYNE_MISE_API_TOKEN:-${ARGUS_MISE_API_TOKEN:-}}" \
MNEMOSYNE_MISE_MEDIA_ROOT="${MNEMOSYNE_MISE_MEDIA_ROOT:-${ARGUS_MISE_MEDIA_ROOT:-$HOME/ai-workspace/argus/data/mise-media}}" \
  bash "$ROOT/scripts/wire-mise.sh"

upsert MNEMOSYNE_PLUTUS_AUTO_LINK "true"
upsert MNEMOSYNE_DB "${MNEMOSYNE_DB:-$ROOT/mnemosyne.db}"

if [[ -n "${PLUTUS_MISE_HOOK_TENANT_ID:-}" ]]; then
  upsert MNEMOSYNE_PLUTUS_TENANT_ID "${MNEMOSYNE_PLUTUS_TENANT_ID:-$PLUTUS_MISE_HOOK_TENANT_ID}"
fi
if [[ -n "${PLUTUS_API_TOKEN:-}" && -z "$(grep -E '^MNEMOSYNE_PLUTUS_API_TOKEN=' "$ENV_FILE" 2>/dev/null || true)" ]]; then
  upsert MNEMOSYNE_PLUTUS_API_TOKEN "$PLUTUS_API_TOKEN"
fi
if [[ -n "${PLUTUS_SAAS_PUBLIC_URL:-}" ]]; then
  upsert MNEMOSYNE_PLUTUS_URL "${MNEMOSYNE_PLUTUS_URL:-$PLUTUS_SAAS_PUBLIC_URL}"
fi

DOGFOOD_EMAIL="${MNEMOSYNE_DOGFOOD_EMAIL:-bench@example.com}"
DOGFOOD_PASSWORD="${MNEMOSYNE_DOGFOOD_PASSWORD:-dogfood-suite}"
upsert MNEMOSYNE_DOGFOOD_EMAIL "$DOGFOOD_EMAIL"
upsert MNEMOSYNE_DOGFOOD_PASSWORD "$DOGFOOD_PASSWORD"

echo "==> Ensure dogfood user password"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true
MNEMOSYNE_DOGFOOD_EMAIL="$DOGFOOD_EMAIL" \
MNEMOSYNE_DOGFOOD_PASSWORD="$DOGFOOD_PASSWORD" \
python3 - <<PY
import os
from pathlib import Path
import sys
sys.path.insert(0, "${ROOT}/src")
from mnemosyne import auth, config, db

email = os.environ["MNEMOSYNE_DOGFOOD_EMAIL"]
password = os.environ["MNEMOSYNE_DOGFOOD_PASSWORD"]
conn = db.connect(config.DB_PATH)
row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
if row is None:
    user = auth.create_user(conn, email, password)
    print(f"  created user {email} (id={user['id']})")
else:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE email = ?",
        (auth.hash_password(password), email),
    )
    conn.commit()
    print(f"  reset password for {email}")
PY

if systemctl --user is-active mnemosyne >/dev/null 2>&1; then
  echo "==> restart mnemosyne"
  systemctl --user restart mnemosyne
  sleep 2
fi

if [[ -x "$ROOT/../plutus/scripts/install-sync-mise-cron.sh" ]]; then
  echo "==> Mise media sync cron (every 6h)"
  bash "$ROOT/../plutus/scripts/install-sync-mise-cron.sh" || true
fi

echo "==> Suite integration wired"
echo "    dogfood: $DOGFOOD_EMAIL / (see MNEMOSYNE_DOGFOOD_PASSWORD in .env)"
echo "    import:  bash scripts/import-mise-gallery.sh <gallery_id>"
echo "    full loop: cd ~/ai-workspace/plutus && bash scripts/dogfood-suite-loop.sh 1"