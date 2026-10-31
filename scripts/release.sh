#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_REPO="${DOCKER_IMAGE:-192.168.1.145:30142/admin-allan/hikvision-timelapse-generator}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh [major|minor|patch|x.y.z] [--dry-run]

Release a new application version with SemVer, Docker image publishing, and Git tags.

Examples:
  ./scripts/release.sh
  ./scripts/release.sh patch
  ./scripts/release.sh minor
  ./scripts/release.sh major
  ./scripts/release.sh 1.2.3
  ./scripts/release.sh patch --dry-run

Notes:
  - Defaults to a patch release when no bump is supplied.
  - Creates Git tags like v1.2.3, v1.2, and v1.
  - Publishes Docker tags like 1.2.3, 1.2, 1, and latest.
  - The image repository can be overridden with DOCKER_IMAGE.
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN="true"
      ;;
    major|minor|patch)
      BUMP_TYPE="$arg"
      ;;
    *)
      if [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        EXPLICIT_VERSION="$arg"
      else
        echo "Unknown argument: $arg" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${BUMP_TYPE:-}" && -z "${EXPLICIT_VERSION:-}" ]]; then
  BUMP_TYPE="patch"
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "This script must be run from inside a git repository." >&2
  exit 1
fi

if [[ "$DRY_RUN" != "true" ]] && (! git diff --quiet || ! git diff --cached --quiet); then
  echo "The working tree is not clean. Commit or stash changes before creating a release." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to build and push the release image." >&2
  exit 1
fi

CURRENT_TAG="$(git tag --list 'v*' --sort=-version:refname | head -n 1 || true)"
CURRENT_VERSION="${CURRENT_TAG#v}"

if [[ -n "${EXPLICIT_VERSION:-}" ]]; then
  NEXT_VERSION="$EXPLICIT_VERSION"
elif [[ -n "$CURRENT_VERSION" ]]; then
  IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"
  case "$BUMP_TYPE" in
    major)
      ((major += 1)); minor=0; patch=0
      ;;
    minor)
      ((minor += 1)); patch=0
      ;;
    patch|*)
      ((patch += 1))
      ;;
  esac
  NEXT_VERSION="${major}.${minor}.${patch}"
else
  NEXT_VERSION="0.1.0"
fi

MAJOR="${NEXT_VERSION%%.*}"
REST="${NEXT_VERSION#*.}"
MINOR="${REST%%.*}"
PATCH="${NEXT_VERSION##*.}"

GIT_TAG="v${NEXT_VERSION}"
MINOR_TAG="v${MAJOR}.${MINOR}"
MAJOR_TAG="v${MAJOR}"

print_release_summary() {
  cat <<EOF
Release plan:
  - version: ${NEXT_VERSION}
  - git tags: ${GIT_TAG}, ${MINOR_TAG}, ${MAJOR_TAG}
  - docker image: ${IMAGE_REPO}
  - docker tags: ${NEXT_VERSION}, ${MAJOR}.${MINOR}, ${MAJOR}, latest
EOF
}

ensure_git_tag() {
  local tag="$1"
  local message="$2"

  if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    echo "Tag ${tag} already exists; skipping."
  else
    git tag -a "$tag" -m "$message"
    echo "Created git tag: $tag"
  fi
}

remote_tag_exists() {
  local tag="$1"
  git ls-remote --tags "$GIT_REMOTE" "refs/tags/${tag}" >/dev/null 2>&1
}

push_tag() {
  local tag="$1"
  if remote_tag_exists "$tag"; then
    echo "Remote git tag ${tag} already exists; skipping."
    return 0
  fi

  git push "$GIT_REMOTE" "refs/tags/${tag}"
  echo "Pushed git tag: $tag"
}

docker_tag_exists() {
  local reference="$1"
  docker manifest inspect "$reference" >/dev/null 2>&1
}

if [[ "$DRY_RUN" == "true" ]]; then
  print_release_summary
  echo "Dry run only; no files changed, no Docker image pushed, and no tags created."
  exit 0
fi

print_release_summary

# Ensure the branch is up to date before release.
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ -n "$CURRENT_BRANCH" && "$CURRENT_BRANCH" != "HEAD" ]]; then
  echo "Releasing from branch: $CURRENT_BRANCH"
fi

# Create and push Git tags.
ensure_git_tag "$GIT_TAG" "Release ${GIT_TAG}"
ensure_git_tag "$MINOR_TAG" "Release ${MINOR_TAG}"
ensure_git_tag "$MAJOR_TAG" "Release ${MAJOR_TAG}"

push_tag "$GIT_TAG"
if [[ "$MINOR_TAG" != "$GIT_TAG" ]]; then
  push_tag "$MINOR_TAG"
fi
if [[ "$MAJOR_TAG" != "$MINOR_TAG" && "$MAJOR_TAG" != "$GIT_TAG" ]]; then
  push_tag "$MAJOR_TAG"
fi

# Build and push Docker image with semver-aligned tags.
docker build \
  -t "${IMAGE_REPO}:${NEXT_VERSION}" \
  -t "${IMAGE_REPO}:${MAJOR}.${MINOR}" \
  -t "${IMAGE_REPO}:${MAJOR}" \
  -t "${IMAGE_REPO}:latest" \
  .

for tag in "${NEXT_VERSION}" "${MAJOR}.${MINOR}" "${MAJOR}" "latest"; do
  reference="${IMAGE_REPO}:${tag}"
  if docker_tag_exists "$reference"; then
    echo "Docker tag ${reference} already exists in the registry; skipping push."
    continue
  fi

  docker push "$reference"
  echo "Pushed Docker tag: ${reference}"
done

cat <<EOF

✅ Release complete
Version: ${NEXT_VERSION}
Git tags: ${GIT_TAG}, ${MINOR_TAG}, ${MAJOR_TAG}
Docker tags: ${NEXT_VERSION}, ${MAJOR}.${MINOR}, ${MAJOR}, latest
Docker image: ${IMAGE_REPO}
EOF
