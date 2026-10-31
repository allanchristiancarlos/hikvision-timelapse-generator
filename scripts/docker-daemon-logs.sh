#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${HIKVISION_TIMELAPSE_GENERATOR_CONTAINER_NAME:-hikvision-timelapse-generator-daemon}"

exec docker logs -f "$CONTAINER_NAME"
