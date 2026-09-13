#!/usr/bin/env bash
set -Eeuo pipefail

# Minecraft-safe PantryBot writer fence.
#
# This script is intended to be the forced command reached through the GCP
# witness reverse SSH path. It fences only the PantryBot PostgreSQL StatefulSets
# in the local home cluster. It deliberately does not stop k3s, containerd, a
# node, or the Minecraft workload. Failure to prove the named database pods are
# gone is a failed fence; callers must not promote the other database.

NAMESPACE="pantry-bot"
STATEFULSETS=(
  "postgres-authority"
  "postgres-authority-home-failback"
  "postgres-authority-home-return"
)
SERVICES=(
  "postgres-authority"
  "postgres-authority-home-failback"
  "postgres-authority-home-return"
)
TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: fence-pantry-postgres.sh

Scale down and remove only the named home PantryBot PostgreSQL StatefulSets,
then verify that their services have no endpoints. This is a destructive
fencing operation; do not use it as a health check.
USAGE
  exit 0
fi

[[ "${1:-}" == "--confirm" && "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}

fail() {
  echo "fence_status=failed reason=$1" >&2
  exit 1
}

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
kubectl version --request-timeout=5s >/dev/null 2>&1 || fail "kubernetes_api_unavailable"

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

(( found == 1 )) || fail "no_pantry_postgres_statefulset_found"

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  remaining=0
  for statefulset in "${STATEFULSETS[@]}"; do
    if kubectl -n "$NAMESPACE" get pod "${statefulset}-0" >/dev/null 2>&1; then
      remaining=$((remaining + 1))
    fi
  done
  (( remaining == 0 )) && break
  (( $(date +%s) < deadline )) || fail "postgres_pod_still_present"
  sleep 1
done

for service in "${SERVICES[@]}"; do
  if ! kubectl -n "$NAMESPACE" get service "$service" >/dev/null 2>&1; then
    continue
  fi
  addresses="$(kubectl -n "$NAMESPACE" get endpoints "$service" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] || fail "service_has_endpoints:$service"
done

echo "fence_status=passed scope=pantry-bot-postgres namespace=$NAMESPACE"
