#!/usr/bin/env bash
set -Eeuo pipefail

# Fence the Oracle-hosted PantryBot PostgreSQL writer from the home controller.
# This is intentionally a narrow SSH transport: it targets one named
# StatefulSet and never stops k3s, touches PVCs, or accepts an arbitrary command.

NAMESPACE="pantry-bot"
STATEFULSET="postgres-authority-standby-reseed-v2"
SERVICE="postgres-authority-standby-reseed-v2"
ORACLE_HOST="${PANTRY_ORACLE_FENCE_HOST:-100.78.181.15}"
ORACLE_USER="${PANTRY_ORACLE_FENCE_USER:-ubuntu}"
SSH_KEY="${PANTRY_ORACLE_FENCE_KEY:-/home/chase/.ssh/pantry-bot-oracle}"
KNOWN_HOSTS="${PANTRY_ORACLE_FENCE_KNOWN_HOSTS:-/home/chase/.ssh/known_hosts}"
SSH_BIN="${PANTRY_ORACLE_FENCE_SSH:-/usr/bin/ssh}"
TIMEOUT_SECONDS="${PANTRY_ORACLE_FENCE_TIMEOUT_SECONDS:-45}"

fail() {
  echo "fence_status=failed reason=$1" >&2
  exit 1
}

[[ "$ORACLE_HOST" =~ ^[0-9.]+$ ]] || fail "invalid_oracle_host"
[[ "$ORACLE_USER" == "ubuntu" ]] || fail "invalid_oracle_user"
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
[[ -x "$SSH_BIN" ]] || fail "ssh_unavailable"
[[ -r "$SSH_KEY" ]] || fail "ssh_key_unreadable"
[[ -r "$KNOWN_HOSTS" ]] || fail "known_hosts_unreadable"

remote_kubectl() {
  "$SSH_BIN" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
    "$ORACLE_USER@$ORACLE_HOST" sudo -n kubectl "$@"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: fence-pantry-postgres-oracle.sh --confirm
       fence-pantry-postgres-oracle.sh --dry-run

Fence only the Oracle PantryBot PostgreSQL StatefulSet used by the standby-reseed
transport. The adapter never stops k3s and never deletes PVCs, secrets, routes,
or arbitrary resources.
USAGE
  exit 0
fi

mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}

remote_kubectl version --request-timeout=5s >/dev/null || fail "oracle_kubernetes_api_unavailable"
if ! remote_kubectl -n "$NAMESPACE" get statefulset "$STATEFULSET" >/dev/null 2>&1; then
  echo "fence_status=passed scope=oracle-pantry-postgres namespace=$NAMESPACE reason=not_found"
  exit 0
fi

if [[ "$mode" == "--dry-run" ]]; then
  echo "fence_status=dry-run scope=oracle-pantry-postgres namespace=$NAMESPACE statefulset=$STATEFULSET"
  exit 0
fi

remote_kubectl -n "$NAMESPACE" scale statefulset "$STATEFULSET" --replicas=0 >/dev/null
pod="${STATEFULSET}-0"
if remote_kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
  remote_kubectl -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null
fi

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while remote_kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
  (( $(date +%s) < deadline )) || fail "oracle_postgres_pod_still_present"
  sleep 1
done

if remote_kubectl -n "$NAMESPACE" get service "$SERVICE" >/dev/null 2>&1; then
  addresses="$(remote_kubectl -n "$NAMESPACE" get endpoints "$SERVICE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] || fail "oracle_postgres_service_has_endpoints"
fi

echo "fence_status=passed scope=oracle-pantry-postgres namespace=$NAMESPACE"
