#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_NAME="${HOUSE_SCREENSHOTS_IMAGE:-house-screenshots}"
ENV_FILE="${HOUSE_SCREENSHOTS_ENV_FILE:-$ROOT_DIR/.env}"
CONTAINER_NAME="${HOUSE_SCREENSHOTS_CONTAINER_NAME:-house-screenshots-daemon}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  echo "Create it from .env.example first." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/screenshots"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "Container already exists: $CONTAINER_NAME"
  echo "Use scripts/docker-daemon-stop.sh first, or remove it manually."
  exit 1
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -v "$ROOT_DIR/screenshots:/app/screenshots" \
  "$IMAGE_NAME" "$@"

echo "Started daemon container: $CONTAINER_NAME"
