#!/usr/bin/env bash
# Operator checklist: env + health + optional plutus-generate on a ready album.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${MNEMOSYNE_PORT:-8000}"
BASE="${MNEMOSYNE_URL:-http://127.0.0.1:${PORT}}"
BASE="${BASE%/}"

echo "==> validate-env"
bash "$ROOT/scripts/validate-env.sh" 2>/dev/null || echo "  (validate-env optional for local dogfood)"

echo "==> /healthz"
curl -sf "$BASE/healthz" | python3 -m json.tool | head -25

echo "==> Plutus cross-sell wiring"
python3 - <<'PY'
from mnemosyne import config, plutus_api

base = config.PLUTUS_URL
tid = config.PLUTUS_TENANT_ID
tok = bool(config.PLUTUS_API_TOKEN)
print(f"  MNEMOSYNE_PLUTUS_URL={base or '(unset)'}")
print(f"  MNEMOSYNE_PLUTUS_TENANT_ID={tid or '(unset)'}")
print(f"  MNEMOSYNE_PLUTUS_API_TOKEN={'set' if tok else 'unset'}")
print(f"  plutus_api.configured={plutus_api.configured()}")
if not plutus_api.configured():
    raise SystemExit(
        "Wire Plutus: MNEMOSYNE_PLUTUS_URL=https://plutus.kleephotography.com "
        "MNEMOSYNE_PLUTUS_API_TOKEN=<admin> MNEMOSYNE_PLUTUS_TENANT_ID=flow-studio"
    )
PY

if [[ -n "${MNEMOSYNE_ALBUM_ID:-}" && -n "${PLUTUS_RUN_ID:-}" ]]; then
  echo "==> attach offer (album=${MNEMOSYNE_ALBUM_ID}, run=${PLUTUS_RUN_ID})"
  bash "$ROOT/scripts/dogfood-plutus-link.sh" "$MNEMOSYNE_ALBUM_ID" "$PLUTUS_RUN_ID"
else
  cat <<EOF

Next — after a suite loop or Argus pipeline gives you a plutus_run_id:

  export MNEMOSYNE_DOGFOOD_EMAIL=you@example.com
  export MNEMOSYNE_DOGFOOD_PASSWORD=...
  export MNEMOSYNE_ALBUM_ID=<ready album id>
  export PLUTUS_RUN_ID=<from pipeline>
  bash scripts/dogfood-plutus-link.sh

Or run the full cross-repo loop from plutus:

  cd ~/ai-workspace/plutus
  MNEMOSYNE_ALBUM_ID=<id> bash scripts/dogfood-suite-loop.sh <mise_gallery_id>

EOF
fi

echo "==> Mnemosyne Plutus prod dogfood gate OK"