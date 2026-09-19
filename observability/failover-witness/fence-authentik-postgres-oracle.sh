#!/usr/bin/env bash
set -Eeuo pipefail

# Local Oracle Authentik PostgreSQL fence used when Oracle loses authority.
# Resource identity is fixed; this never deletes PVCs, Secrets, or k3s.
NAMESPACE="auth"
STATEFULSET="auth-postgresql-standby"
SERVICE="auth-postgresql-standby"
KUBECTL_BIN="${AUTHENTIK_KUBECTL:-kubectl}"
TIMEOUT_SECONDS="${AUTHENTIK_POSTGRES_FENCE_TIMEOUT_SECONDS:-60}"

fail() { echo "fence_status=failed reason=$1" >&2; exit 1; }
mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || fail "explicit_confirmation_required"
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
"$KUBECTL_BIN" version --request-timeout=5s >/dev/null 2>&1 || fail "kubernetes_api_unavailable"
"$KUBECTL_BIN" -n "$NAMESPACE" get statefulset "$STATEFULSET" >/dev/null 2>&1 || fail "no_oracle_authentik_postgres_statefulset"
"$KUBECTL_BIN" -n "$NAMESPACE" get service "$SERVICE" >/dev/null 2>&1 || fail "no_oracle_authentik_postgres_service"
selector="$($KUBECTL_BIN -n "$NAMESPACE" get service "$SERVICE" -o jsonpath='{.spec.selector}' 2>/dev/null || true)"
[[ "$selector" == *'auth-postgresql-standby'* ]] || fail "oracle_authentik_service_selector_mismatch"
if [[ "$mode" == "--dry-run" ]]; then
  echo "fence_status=dry-run scope=oracle-authentik-postgres namespace=$NAMESPACE statefulset=$STATEFULSET service=$SERVICE"
  exit 0
fi
"$KUBECTL_BIN" -n "$NAMESPACE" scale statefulset "$STATEFULSET" --replicas=0 >/dev/null
pod="$STATEFULSET-0"
if "$KUBECTL_BIN" -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
  "$KUBECTL_BIN" -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null || true
fi
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while "$KUBECTL_BIN" -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
  (( $(date +%s) < deadline )) || fail "oracle_authentik_postgres_pod_still_present"
  sleep 1
done
while :; do
  addresses="$($KUBECTL_BIN -n "$NAMESPACE" get endpoints "$SERVICE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] && break
  (( $(date +%s) < deadline )) || fail "oracle_authentik_postgres_service_has_endpoints"
  sleep 1
done
echo "fence_status=passed scope=oracle-authentik-postgres namespace=$NAMESPACE statefulset=$STATEFULSET service=$SERVICE"
