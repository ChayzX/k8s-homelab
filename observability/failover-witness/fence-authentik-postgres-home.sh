#!/usr/bin/env bash
set -Eeuo pipefail

# Fence only the Home Authentik PostgreSQL writer before a separately
# authorized Oracle promotion. Resource identity is fixed so this adapter
# cannot be retargeted at another database, standby, or namespace.
# This is a Kubernetes availability fence, not physical host fencing.
NAMESPACE="auth"
STATEFULSET="auth-postgresql-home-primary"
SERVICE="auth-postgresql-home-primary"
KUBECTL_BIN="${AUTHENTIK_KUBECTL:-kubectl}"
TIMEOUT_SECONDS="${AUTHENTIK_POSTGRES_FENCE_TIMEOUT_SECONDS:-60}"

fail() {
  echo "fence_status=failed reason=$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: fence-authentik-postgres-home.sh --confirm
       fence-authentik-postgres-home.sh --dry-run

Fence only the Home Authentik PostgreSQL StatefulSet and wait for its Service
to have no endpoints. The adapter never deletes a PVC, stops k3s, changes
Secrets, or accepts a resource identity from the environment.
USAGE
}

if [[ "$#" == 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
fi

mode="${1-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"

"$KUBECTL_BIN" version --request-timeout=5s >/dev/null 2>&1 || fail "kubernetes_api_unavailable"
"$KUBECTL_BIN" -n "$NAMESPACE" get statefulset "$STATEFULSET" >/dev/null 2>&1 || fail "no_home_authentik_postgres_statefulset"
"$KUBECTL_BIN" -n "$NAMESPACE" get service "$SERVICE" >/dev/null 2>&1 || fail "no_home_authentik_postgres_service"
selector="$($KUBECTL_BIN -n "$NAMESPACE" get service "$SERVICE" -o jsonpath='{.spec.selector}' 2>/dev/null || true)"
[[ "$selector" == *'auth-postgresql-home-primary'* ]] || fail "home_authentik_service_selector_mismatch"

if [[ "$mode" == "--dry-run" ]]; then
  echo "fence_status=dry-run scope=home-authentik-postgres namespace=$NAMESPACE statefulset=$STATEFULSET service=$SERVICE"
  exit 0
fi

"$KUBECTL_BIN" -n "$NAMESPACE" scale statefulset "$STATEFULSET" --replicas=0 >/dev/null
pod="$STATEFULSET-0"
if "$KUBECTL_BIN" -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
  "$KUBECTL_BIN" -n "$NAMESPACE" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null || true
fi

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while "$KUBECTL_BIN" -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
  (( $(date +%s) < deadline )) || fail "home_authentik_postgres_pod_still_present"
  sleep 1
done

while :; do
  addresses="$("$KUBECTL_BIN" -n "$NAMESPACE" get endpoints "$SERVICE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] && break
  (( $(date +%s) < deadline )) || fail "home_authentik_postgres_service_has_endpoints"
  sleep 1
done

echo "fence_status=passed scope=home-authentik-postgres namespace=$NAMESPACE statefulset=$STATEFULSET service=$SERVICE"
