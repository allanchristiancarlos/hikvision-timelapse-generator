#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_NAME="${HIKVISION_TIMELAPSE_GENERATOR_IMAGE:-hikvision-timelapse-generator}"

cd "$ROOT_DIR"

docker build -t "$IMAGE_NAME" .

echo "Built image: $IMAGE_NAME"
