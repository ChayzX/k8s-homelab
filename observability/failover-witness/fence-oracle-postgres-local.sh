#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-local PantryBot writer-domain fence, used before Oracle promotion
# and on lease loss. Fences every PantryBot PostgreSQL StatefulSet on Oracle
# (the live one plus every historical name, so a lost env override can never
# aim the fence at an already-dead object and "pass"), the DB-writing apps,
# and the PantryBot-App tunnel connector. Never stops k3s or touches a PVC.
# Proof and failure semantics live in pantry-writer-fence.sh.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

ORACLE_PANTRY_STATEFULSETS="postgres-authority-standby-oracle-v2 postgres-authority-standby-oracle postgres-authority-standby postgres-authority-standby-home postgres-authority-standby-home-v2 postgres-authority-standby-reseed postgres-authority-standby-reseed-v2"
# Must stay the complement of what the promoter scales up (minus the always-on,
# DB-less pantry-commands-site); test_pantry_writer_fence.py enforces it.
PANTRY_WRITER_DEPLOYMENTS="pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared"

statefulsets="${PANTRY_FENCE_STATEFULSETS:-$ORACLE_PANTRY_STATEFULSETS}"
# Legacy single-name override from postgres-fence.env: always included.
if [[ -n "${PANTRY_STANDBY_STATEFULSET:-}" && " $statefulsets " != *" $PANTRY_STANDBY_STATEFULSET "* ]]; then
  statefulsets="$PANTRY_STANDBY_STATEFULSET $statefulsets"
fi

FENCE_SCOPE=oracle-local \
FENCE_KUBECTL="${ORACLE_KUBECTL:-kubectl}" \
FENCE_NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}" \
FENCE_STATEFULSETS="$statefulsets" \
FENCE_DEPLOYMENTS="${PANTRY_FENCE_DEPLOYMENTS-$PANTRY_WRITER_DEPLOYMENTS}" \
FENCE_TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}" \
FENCE_ALLOW_UNREACHABLE=0 \
  exec "$SCRIPT_DIR/pantry-writer-fence.sh" "$@"
