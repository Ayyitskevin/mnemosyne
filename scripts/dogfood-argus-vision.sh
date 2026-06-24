#!/usr/bin/env bash
# Operator gate: Argus health + one mnemosyne vision delegation call.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

ARGUS_URL="${MNEMOSYNE_ARGUS_URL:-${ARGUS_URL:-}}"
SAMPLE="${MNEMOSYNE_ARGUS_SAMPLE:-$ROOT/scratch/fnb_gallery/00.jpg}"

if [[ -z "$ARGUS_URL" ]]; then
  echo "Wire Argus first: bash scripts/wire-argus.sh" >&2
  exit 1
fi

echo "==> Argus health"
curl -sf "${ARGUS_URL%/}/healthz" | python3 -m json.tool || {
  echo "Argus not reachable at $ARGUS_URL" >&2
  exit 1
}

if [[ ! -f "$SAMPLE" ]]; then
  echo "Sample image missing: $SAMPLE" >&2
  exit 1
fi

echo "==> mnemosyne vision via Argus ($SAMPLE)"
export MNEMOSYNE_ARGUS_SAMPLE="$SAMPLE"
PYTHONPATH=src python3 - <<'PY'
import os
from mnemosyne import config, vision

assert config.ARGUS_URL, "MNEMOSYNE_ARGUS_URL unset"
backend = (config.VISION_BACKEND or "").strip().lower() or "auto"
print(f"  vision_backend={backend or 'argus-default'} argus_url={config.ARGUS_URL}")
print(f"  delegation_mode={config.ARGUS_DELEGATION_MODE}")

sample = os.environ["MNEMOSYNE_ARGUS_SAMPLE"]
out = vision.analyze_one(sample, theme="food")
print(f"  scene={out['scene']!r} hero_score={out['hero_score']}")
PY

echo "==> Mnemosyne Argus vision dogfood OK"