#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${HOUSE_SCREENSHOTS_CONTAINER_NAME:-house-screenshots-daemon}"

exec docker logs -f "$CONTAINER_NAME"
