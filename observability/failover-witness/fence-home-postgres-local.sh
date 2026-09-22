#!/usr/bin/env bash
set -Eeuo pipefail

# Home-local PantryBot writer-domain fence (Home promoter's local fence and
# lease-loss self-fence). Local admin kubectl; never claims unreachable.
# Lists must match fence-home-direct-from-oracle.sh (test enforced).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

HOME_PANTRY_STATEFULSETS="postgres-authority-standby-home-canada postgres-authority-home-v2 postgres-authority-home-failback postgres-authority-home postgres-authority-home-return postgres-authority"
PANTRY_WRITER_DEPLOYMENTS="pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared pantry-bot"

# PANTRY_FENCE_APPS_ONLY=1: apps + connector only (DB positively a standby).
statefulsets="$HOME_PANTRY_STATEFULSETS"
[[ "${PANTRY_FENCE_APPS_ONLY:-0}" == 1 ]] && statefulsets=""

FENCE_SCOPE=home-local \
FENCE_KUBECTL="${HOME_LOCAL_KUBECTL:-kubectl}" \
FENCE_NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}" \
FENCE_STATEFULSETS="$statefulsets" \
FENCE_APPS_ONLY="${PANTRY_FENCE_APPS_ONLY:-0}" \
FENCE_DEPLOYMENTS="$PANTRY_WRITER_DEPLOYMENTS" \
FENCE_TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}" \
FENCE_ALLOW_UNREACHABLE=0 \
  exec "$SCRIPT_DIR/pantry-writer-fence.sh" "$@"
