#!/usr/bin/env bash
# Host loop for k3s sites (#191): runs one pantry-standby-follower.sh
# iteration inside the local PostgreSQL container every FOLLOWER_INTERVAL
# seconds (local unix-socket access, state persisted in PGDATA). No sidecar,
# so installing it never restarts the database pod.
set -uo pipefail
: "${FOLLOWER_POD:?}" "${FOLLOWER_SITE:?}" "${FOLLOWER_PEERS:?}"
NS="${PANTRY_NAMESPACE:-pantry-bot}"
SCRIPT="${FOLLOWER_SCRIPT:-/usr/local/lib/failover-witness/pantry-standby-follower.sh}"
while :; do
  kubectl -n "$NS" exec -i "$FOLLOWER_POD" -c postgres -- \
    env SITE="$FOLLOWER_SITE" PEERS="$FOLLOWER_PEERS" ONESHOT=1 REPOINT_AFTER="${FOLLOWER_REPOINT_AFTER:-20}" \
    sh -s < "$SCRIPT" || true
  sleep "${FOLLOWER_INTERVAL:-5}"
done
