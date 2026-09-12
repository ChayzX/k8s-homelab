#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-side PantryBot PostgreSQL fence for controlled return-home.
# Targets only the Oracle PantryBot standby/authority StatefulSet.

NAMESPACE="pantry-bot"
STATEFULSET="postgres-authority-standby"
SERVICE="postgres-authority-standby"
TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: fence-pantry-postgres-oracle.sh --confirm

Scale down and remove only the Oracle PantryBot PostgreSQL StatefulSet, then
verify that its Service has no endpoints. This is destructive fencing, not a
health check.
USAGE
  exit 0
fi

fail() {
  echo "fence_status=failed reason=$1" >&2
  exit 1
}

[[ "${1:-}" == "--confirm" && "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
kubectl version --request-timeout=5s >/dev/null 2>&1 || fail "kubernetes_api_unavailable"

kubectl -n "$NAMESPACE" get statefulset "$STATEFULSET" >/dev/null 2>&1 || \
  fail "oracle_pantry_postgres_statefulset_not_found"
kubectl -n "$NAMESPACE" scale statefulset "$STATEFULSET" --replicas=0 >/dev/null
pod="${STATEFULSET}-0"
if kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
  kubectl -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null
fi

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
  (( $(date +%s) < deadline )) || fail "oracle_postgres_pod_still_present"
  sleep 1
done

addresses="$(kubectl -n "$NAMESPACE" get endpoints "$SERVICE" \
  -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
[[ -z "$addresses" ]] || fail "service_has_endpoints:$SERVICE"

echo "fence_status=passed scope=pantry-bot-postgres-oracle namespace=$NAMESPACE"
