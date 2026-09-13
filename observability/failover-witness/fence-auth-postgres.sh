#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="auth"
STATEFULSETS=("auth-postgresql" "auth-postgresql-chasebot-standby")
SERVICES=("auth-postgresql" "auth-postgresql-chasebot-standby")
TIMEOUT_SECONDS="${AUTH_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}"

[[ "${1:-}" == "--confirm" && "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}
fail() { echo "fence_status=failed reason=$1" >&2; exit 1; }
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail invalid_timeout
kubectl version --request-timeout=5s >/dev/null 2>&1 || fail kubernetes_api_unavailable

found=0
for statefulset in "${STATEFULSETS[@]}"; do
  if ! kubectl -n "$NAMESPACE" get statefulset "$statefulset" >/dev/null 2>&1; then
    continue
  fi
  found=1
  kubectl -n "$NAMESPACE" patch statefulset "$statefulset" --type=merge \
    -p='{"spec":{"replicas":0}}' >/dev/null
  pod="${statefulset}-0"
  if kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null
  fi
done
(( found == 1 )) || fail no_auth_postgres_statefulset_found

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  remaining=0
  for statefulset in "${STATEFULSETS[@]}"; do
    kubectl -n "$NAMESPACE" get pod "${statefulset}-0" >/dev/null 2>&1 && remaining=$((remaining + 1))
  done
  (( remaining == 0 )) && break
  (( $(date +%s) < deadline )) || fail auth_postgres_pod_still_present
  sleep 1
done

for service in "${SERVICES[@]}"; do
  if kubectl -n "$NAMESPACE" get service "$service" >/dev/null 2>&1; then
    addresses="$(kubectl -n "$NAMESPACE" get endpoints "$service" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
    [[ -z "$addresses" ]] || fail "service_has_endpoints:$service"
  fi
done
echo "fence_status=passed scope=auth-postgres namespace=$NAMESPACE"
