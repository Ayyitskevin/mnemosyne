#!/usr/bin/env bash
# Wire mnemosyne to S3-compatible storage (local MinIO — same pattern as Plutus dogfood).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${MNEMOSYNE_ENV_FILE:-$ROOT/.env}"
PLUTUS_ROOT="${PLUTUS_ROOT:-$HOME/ai-workspace/plutus}"
COMPOSE="${MNEMOSYNE_MINIO_COMPOSE:-$PLUTUS_ROOT/ops/minio-compose.yml}"
BUCKET="${MNEMOSYNE_S3_BUCKET:-mnemosyne-galleries}"
ENDPOINT="${MNEMOSYNE_S3_ENDPOINT:-http://127.0.0.1:9000}"
ACCESS_KEY="${MNEMOSYNE_S3_ACCESS_KEY:-plutus}"
SECRET_KEY="${MNEMOSYNE_S3_SECRET_KEY:-plutus-dev-secret}"

if [[ -f "$COMPOSE" ]] && command -v docker >/dev/null 2>&1; then
  echo "==> Start MinIO ($COMPOSE)"
  MINIO_ROOT_USER="$ACCESS_KEY" MINIO_ROOT_PASSWORD="$SECRET_KEY" \
    docker compose -f "$COMPOSE" up -d
else
  echo "==> MinIO compose not found — assuming S3 endpoint already running at $ENDPOINT"
fi

echo "==> Ensure boto3"
if [[ -d "$ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  pip install -q -e '.[r2]'
else
  pip install -q boto3
fi

echo "==> Wait for MinIO / S3 endpoint"
deadline=$((SECONDS + 30))
until curl -sf "$ENDPOINT/minio/health/live" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "endpoint not ready at $ENDPOINT" >&2
    exit 1
  fi
  sleep 1
done

ENDPOINT="$ENDPOINT" BUCKET="$BUCKET" ACCESS_KEY="$ACCESS_KEY" SECRET_KEY="$SECRET_KEY" \
python3 - <<'PY'
import os
import boto3
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url=os.environ["ENDPOINT"],
    aws_access_key_id=os.environ["ACCESS_KEY"],
    aws_secret_access_key=os.environ["SECRET_KEY"],
    region_name="auto",
)
bucket = os.environ["BUCKET"]
try:
    client.head_bucket(Bucket=bucket)
except ClientError:
    client.create_bucket(Bucket=bucket)
print("bucket ready:", bucket)
PY

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "MNEMOSYNE_STORAGE_BACKEND": "r2",
    "MNEMOSYNE_R2_ENDPOINT": "${ENDPOINT}",
    "MNEMOSYNE_R2_BUCKET": "${BUCKET}",
    "MNEMOSYNE_R2_ACCESS_KEY_ID": "${ACCESS_KEY}",
    "MNEMOSYNE_R2_SECRET_ACCESS_KEY": "${SECRET_KEY}",
}
lines = env_path.read_text().splitlines() if env_path.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote MinIO/S3 settings to", env_path)
PY

echo "Done — mnemosyne storage wired to ${ENDPOINT} bucket ${BUCKET}"