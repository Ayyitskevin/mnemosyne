#!/usr/bin/env bash
# Deploy mnemosyne to Fly.io (secrets from .env, persistent /data volume).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FLYCTL="${FLYCTL:-flyctl}"
if ! command -v "$FLYCTL" >/dev/null 2>&1; then
  if [[ -x "$HOME/.fly/bin/flyctl" ]]; then
    FLYCTL="$HOME/.fly/bin/flyctl"
  else
    echo "flyctl not found — install: curl -fsSL https://fly.io/install.sh | sh" >&2
    exit 1
  fi
fi

APP="${FLY_APP:-mnemosyne}"
REGION="${FLY_REGION:-iad}"
ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi

if ! "$FLYCTL" auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Fly — run: $FLYCTL auth login" >&2
  exit 1
fi

bash "$ROOT/scripts/validate-env.sh" "$ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ "${MNEMOSYNE_R2_ENDPOINT:-}" == http://127.0.0.1:* ]]; then
  echo "ERROR: .env still points R2 at local MinIO — run scripts/wire-r2.sh with Cloudflare creds first" >&2
  exit 1
fi

if [[ -z "${MNEMOSYNE_PUBLIC_URL:-}" ]]; then
  echo "WARN: MNEMOSYNE_PUBLIC_URL unset — set to https://${APP}.fly.dev before deploy" >&2
fi

echo "==> Ensure Fly app $APP"
if ! "$FLYCTL" apps list --json | python3 -c "
import json, sys
apps = {a['Name'] for a in json.load(sys.stdin)}
sys.exit(0 if '${APP}' in apps else 1)
" 2>/dev/null; then
  "$FLYCTL" apps create "$APP" --org personal
fi

echo "==> Ensure volume mnemosyne_data ($REGION)"
if ! "$FLYCTL" volumes list -a "$APP" --json | python3 -c "
import json, sys
names = {v.get('Name') for v in json.load(sys.stdin)}
sys.exit(0 if 'mnemosyne_data' in names else 1)
" 2>/dev/null; then
  "$FLYCTL" volumes create mnemosyne_data --region "$REGION" --size 1 -a "$APP" -y
fi

echo "==> Sync secrets from $ENV_FILE"
TMP_SECRETS=$(mktemp)
trap 'rm -f "$TMP_SECRETS"' EXIT

python3 - <<'PY' "$ENV_FILE" "$TMP_SECRETS"
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
skip_prefixes = ("#",)
skip_keys = {
    "MNEMOSYNE_PORT",
    "MNEMOSYNE_HOST",
    "MNEMOSYNE_DB",
    "MNEMOSYNE_UPLOAD_DIR",
}
lines_out = []
for line in env_path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key in skip_keys:
        continue
    if not value.strip():
        continue
    lines_out.append(f"{key}={value}")
out_path.write_text("\n".join(lines_out) + "\n")
PY

"$FLYCTL" secrets import -a "$APP" < "$TMP_SECRETS"

echo "==> Deploy"
"$FLYCTL" deploy -a "$APP" --ha=false

echo ""
echo "Done — https://${APP}.fly.dev (set MNEMOSYNE_PUBLIC_URL to your custom domain when ready)"