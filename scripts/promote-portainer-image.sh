#!/usr/bin/env bash
# Promote an image to the branch Portainer deploys from.
#
# Portainer is the only deployer for migrated workloads. CI publishes an image,
# then this script copies the manifest from the current checkout to
# $DEPLOY_BRANCH with exactly one image reference rewritten. Rollback calls it
# again with the previous image, so Git and the cluster cannot disagree.
#
# Usage: promote-portainer-image.sh <manifest> <image-repository> <target-image>
# Writes previous_image and changed to $GITHUB_OUTPUT.
set -euo pipefail

manifest="$1"
image_repository="$2"
target_image="$3"
: "${DEPLOY_BRANCH:?DEPLOY_BRANCH is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

source_manifest="$PWD/$manifest"
repository_pattern="$(printf '%s' "$image_repository" | sed 's/[.[\*^$|]/\\&/g')"
worktree="$RUNNER_TEMP/portainer-deploy"
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'

# Other workflows push to the same branch, so retry from a fresh tip.
for attempt in 1 2 3 4 5; do
  git worktree remove --force "$worktree" 2>/dev/null || true
  git fetch --quiet origin "$DEPLOY_BRANCH"
  git worktree add --quiet --detach "$worktree" FETCH_HEAD

  previous_image="$(sed -nE "s|^ +image: (${repository_pattern}:[^[:space:]]+).*$|\\1|p" "$worktree/$manifest")"
  test "$(printf '%s\n' "$previous_image" | grep -c .)" = 1

  cp "$source_manifest" "$worktree/$manifest"
  sed -i -E "s|^( +image: )${repository_pattern}:[^[:space:]]+|\\1${target_image}|" "$worktree/$manifest"
  test "$(grep -cF "image: ${target_image}" "$worktree/$manifest")" = 1

  git -C "$worktree" add "$manifest"
  if git -C "$worktree" diff --cached --quiet; then
    changed=false
    break
  fi
  git -C "$worktree" commit --quiet -m "$(basename "$(dirname "$manifest")"): promote ${target_image##*:}"
  if git -C "$worktree" push --quiet origin "HEAD:refs/heads/${DEPLOY_BRANCH}"; then
    changed=true
    break
  fi
  if [ "$attempt" = 5 ]; then
    echo "::error::Could not push ${target_image} to ${DEPLOY_BRANCH}" >&2
    exit 1
  fi
  sleep $((attempt * 3))
done

echo "previous_image=$previous_image" >> "$GITHUB_OUTPUT"
echo "changed=$changed" >> "$GITHUB_OUTPUT"
