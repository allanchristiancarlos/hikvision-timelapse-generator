#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${HOUSE_SCREENSHOTS_CONTAINER_NAME:-house-screenshots-daemon}"

if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "Container not found: $CONTAINER_NAME"
  exit 0
fi

docker stop "$CONTAINER_NAME" >/dev/null

docker rm "$CONTAINER_NAME" >/dev/null

echo "Stopped and removed: $CONTAINER_NAME"
