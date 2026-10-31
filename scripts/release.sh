#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_REPO="${DOCKER_IMAGE:-192.168.1.145:30142/admin-allan/hikvision-timelapse-generator}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
RELEASE_BRANCH="main"
MAX_RELEASE_NOTES_ITEMS="12"
PUBLISH_GITHUB_RELEASE="${PUBLISH_GITHUB_RELEASE:-true}"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh [major|minor|patch|x.y.z] [--dry-run]

Release a new application version with SemVer, Docker image publishing, and Git metadata.

Examples:
  ./scripts/release.sh
  ./scripts/release.sh patch
  ./scripts/release.sh minor
  ./scripts/release.sh major
  ./scripts/release.sh 1.2.3
  ./scripts/release.sh patch --dry-run

Notes:
  - Defaults to a patch release when no bump is supplied.
  - Releases are allowed only from the main branch.
  - If you are on another branch, switch to main and merge your changes first.
  - Auto-generates business-focused release notes from recent commit history.
  - Pushes the current branch (if any local commits exist) and release tags to the Git remote.
  - Publishes/updates a GitHub Release for the version tag when gh CLI is available.
  - Set PUBLISH_GITHUB_RELEASE=false to skip GitHub Release publishing.
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

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$RELEASE_BRANCH" ]]; then
  echo "Release blocked: current branch is '${CURRENT_BRANCH}'." >&2
  echo "Releases are only allowed from '${RELEASE_BRANCH}'." >&2
  echo "Switch to '${RELEASE_BRANCH}', merge your changes, then run release again." >&2
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
  echo "Release plan:"
  echo "  - version: ${NEXT_VERSION}"
  echo "  - git tags: ${GIT_TAG}, ${MINOR_TAG}, ${MAJOR_TAG}"
  if [[ -n "$CURRENT_TAG" ]]; then
    echo "  - notes baseline: ${CURRENT_TAG}"
  else
    echo "  - notes baseline: repository start"
  fi
  echo "  - docker image: ${IMAGE_REPO}"
  echo "  - docker tags: ${NEXT_VERSION}, ${MAJOR}.${MINOR}, ${MAJOR}, latest"
  echo "  - github release publishing: ${PUBLISH_GITHUB_RELEASE}"
}

clean_commit_subject() {
  local subject="$1"
  subject="$(echo "$subject" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  subject="$(echo "$subject" | sed -E 's/^[[:alpha:]]+(\([^)]*\))?!?:[[:space:]]*//')"
  subject="$(echo "$subject" | sed -E 's/\b[A-Z]+-[0-9]+\b//g')"
  subject="$(echo "$subject" | sed -E 's/[[:space:]]+/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  printf '%s' "$subject"
}

format_business_note() {
  local summary="$1"
  summary="${summary%[.!?]}"
  local lowered="${summary,,}"

  if [[ "$lowered" == *fix* || "$lowered" == *bug* || "$lowered" == *error* || "$lowered" == *issue* ]]; then
    printf 'Improved reliability: %s.' "$summary"
  elif [[ "$lowered" == *perf* || "$lowered" == *speed* || "$lowered" == *faster* || "$lowered" == *optimi* ]]; then
    printf 'Improved processing efficiency: %s.' "$summary"
  elif [[ "$lowered" == *security* || "$lowered" == *auth* || "$lowered" == *permission* || "$lowered" == *vuln* ]]; then
    printf 'Strengthened operational safety: %s.' "$summary"
  elif [[ "$lowered" == *feat* || "$lowered" == *add* || "$lowered" == *new* || "$lowered" == *support* || "$lowered" == *enable* ]]; then
    printf 'Expanded product capability: %s.' "$summary"
  else
    printf 'Operational improvement: %s.' "$summary"
  fi
}

generate_business_release_notes() {
  local from_tag="$1"
  local range="HEAD"
  local baseline_text="Based on all changes currently in the project."
  local total_commits=0
  local remaining=0
  local subject
  local cleaned
  local notes=()

  if [[ -n "$from_tag" ]]; then
    range="${from_tag}..HEAD"
    baseline_text="Based on updates since ${from_tag}."
  fi

  total_commits="$(git rev-list --count "$range" 2>/dev/null || echo 0)"

  while IFS= read -r subject; do
    [[ -z "$subject" ]] && continue
    cleaned="$(clean_commit_subject "$subject")"
    [[ -z "$cleaned" ]] && cleaned="$subject"
    notes+=("- $(format_business_note "$cleaned")")
  done < <(git log --max-count="$MAX_RELEASE_NOTES_ITEMS" --pretty=format:'%s' "$range")

  if [[ ${#notes[@]} -eq 0 ]]; then
    notes+=("- Routine maintenance release focused on stable day-to-day operations.")
  fi

  if (( total_commits > MAX_RELEASE_NOTES_ITEMS )); then
    remaining=$((total_commits - MAX_RELEASE_NOTES_ITEMS))
    notes+=("- Plus ${remaining} additional maintenance and quality improvements.")
  fi

  echo "Business release notes"
  echo "$baseline_text"
  echo
  for note in "${notes[@]}"; do
    echo "$note"
  done
  echo
  echo "Customer impact: smoother operations with clearer, more dependable outcomes."
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
  [[ -n "$(git ls-remote --tags "$GIT_REMOTE" "refs/tags/${tag}")" ]]
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

push_branch_changes() {
  local branch="$1"

  if [[ -z "$branch" || "$branch" == "HEAD" ]]; then
    echo "Detached HEAD detected; skipping branch push."
    return 0
  fi

  if git rev-parse --verify "${GIT_REMOTE}/${branch}" >/dev/null 2>&1; then
    git push "$GIT_REMOTE" "$branch"
  else
    git push --set-upstream "$GIT_REMOTE" "$branch"
  fi

  echo "Pushed branch: ${branch}"
}

docker_tag_exists() {
  local reference="$1"
  docker manifest inspect "$reference" >/dev/null 2>&1
}

github_release_exists() {
  local tag="$1"
  gh release view "$tag" >/dev/null 2>&1
}

publish_github_release() {
  local tag="$1"
  local title="Release ${tag}"
  local body

  if [[ "$PUBLISH_GITHUB_RELEASE" != "true" ]]; then
    echo "GitHub Release publishing disabled (PUBLISH_GITHUB_RELEASE=${PUBLISH_GITHUB_RELEASE})."
    return 0
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI (gh) is required to publish GitHub Releases." >&2
    echo "Install gh or set PUBLISH_GITHUB_RELEASE=false to skip this step." >&2
    exit 1
  fi

  body="$(cat <<EOF
${BUSINESS_RELEASE_NOTES}

Version details
- Application version: ${NEXT_VERSION}
- Docker tags: ${NEXT_VERSION}, ${MAJOR}.${MINOR}, ${MAJOR}, latest
EOF
)"

  if github_release_exists "$tag"; then
    gh release edit "$tag" --title "$title" --notes "$body"
    echo "Updated GitHub Release: ${tag}"
  else
    gh release create "$tag" --title "$title" --notes "$body"
    echo "Created GitHub Release: ${tag}"
  fi
}

BUSINESS_RELEASE_NOTES="$(generate_business_release_notes "$CURRENT_TAG")"

if [[ "$DRY_RUN" == "true" ]]; then
  print_release_summary
  echo
  echo "$BUSINESS_RELEASE_NOTES"
  echo
  if [[ "$PUBLISH_GITHUB_RELEASE" == "true" ]]; then
    echo "Dry run: would create or update GitHub Release for ${GIT_TAG}."
  else
    echo "Dry run: GitHub Release publishing is disabled."
  fi
  echo "Dry run only; no files changed, no Docker image pushed, and no git refs pushed."
  exit 0
fi

print_release_summary
echo
echo "$BUSINESS_RELEASE_NOTES"

# Ensure the branch is up to date before release.
if [[ -n "$CURRENT_BRANCH" && "$CURRENT_BRANCH" != "HEAD" ]]; then
  echo "Releasing from branch: $CURRENT_BRANCH"
fi

# Push branch changes before creating release tags.
push_branch_changes "$CURRENT_BRANCH"

# Create and push Git tags.
ensure_git_tag "$GIT_TAG" "Release ${GIT_TAG}

${BUSINESS_RELEASE_NOTES}"
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

publish_github_release "$GIT_TAG"

cat <<EOF

✅ Release complete
Version: ${NEXT_VERSION}
Git tags: ${GIT_TAG}, ${MINOR_TAG}, ${MAJOR_TAG}
Docker tags: ${NEXT_VERSION}, ${MAJOR}.${MINOR}, ${MAJOR}, latest
Docker image: ${IMAGE_REPO}
GitHub Release: ${GIT_TAG}

${BUSINESS_RELEASE_NOTES}
EOF
