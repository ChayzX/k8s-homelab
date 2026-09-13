#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-side Authentik PostgreSQL fence for controlled return-home.
# This deliberately targets only the Oracle Authentik standby StatefulSet.

NAMESPACE="auth"
STATEFULSET="auth-postgresql-standby"
SERVICE="auth-postgresql-standby"
TIMEOUT_SECONDS="${AUTH_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}"

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
  fail "oracle_authentik_postgres_statefulset_not_found"
kubectl -n "$NAMESPACE" patch statefulset "$STATEFULSET" --type=merge \
  -p '{"spec":{"replicas":0}}' >/dev/null
pod="${STATEFULSET}-0"
if kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
  kubectl -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null
fi

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
  (( $(date +%s) < deadline )) || fail "oracle_authentik_postgres_pod_still_present"
  sleep 1
done

addresses="$(kubectl -n "$NAMESPACE" get endpoints "$SERVICE" \
  -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
[[ -z "$addresses" ]] || fail "service_has_endpoints:$SERVICE"

echo "fence_status=passed scope=auth-postgres-oracle namespace=$NAMESPACE"
