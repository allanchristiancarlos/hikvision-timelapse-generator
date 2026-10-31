#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_NAME="${HOUSE_SCREENSHOTS_IMAGE:-house-screenshots}"
ENV_FILE="${HOUSE_SCREENSHOTS_ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  echo "Create it from .env.example first." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/screenshots"

exec docker run --rm -it \
  --env-file "$ENV_FILE" \
  -v "$ROOT_DIR/screenshots:/app/screenshots" \
  --entrypoint python \
  "$IMAGE_NAME" \
  daily_pipeline.py --once "$@"
