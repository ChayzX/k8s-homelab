#!/usr/bin/env bash
set -Eeuo pipefail

# Scoped fence of the Home PostgreSQL writer, invoked from Oracle directly
# against minecraftmachine's k3s API using a least-privilege kubeconfig. No
# GCP relay, no ChaseBot involvement — Postgres and the k3s control plane are
# now the same host, so this is a direct, in-cluster-style scoped fence
# exactly like fence-oracle-postgres-local.sh, just over the network.
KUBECTL_BIN="${HOME_KUBECTL:-kubectl}"
KUBECONFIG_PATH="${HOME_FENCER_KUBECONFIG:-/etc/failover-witness/pantry-postgres-fencer-home.kubeconfig}"
NS="${PANTRY_NAMESPACE:-pantry-bot}"
STS="${PANTRY_HOME_STATEFULSET:-postgres-authority-home}"
TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-60}"

fail() {
  echo "fence_status=failed reason=$1" >&2
  exit 1
}

[[ "${1:-}" == "--confirm" && "$#" == 1 ]] || fail "explicit_confirmation_required"
[[ -r "$KUBECONFIG_PATH" ]] || fail "kubeconfig_unreadable"
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
"$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" version --request-timeout=5s >/dev/null 2>&1 || fail "kubernetes_api_unavailable"
"$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" -n "$NS" get statefulset "$STS" >/dev/null 2>&1 || fail "no_home_postgres_statefulset"

"$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" -n "$NS" patch statefulset "$STS" --type=merge -p '{"spec":{"replicas":0}}' >/dev/null
pod="${STS}-0"
if "$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" -n "$NS" get pod "$pod" >/dev/null 2>&1; then
  "$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" -n "$NS" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null || true
fi
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  addresses="$("$KUBECTL_BIN" --kubeconfig="$KUBECONFIG_PATH" -n "$NS" get endpoints "$STS" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] && break
  (( $(date +%s) < deadline )) || fail "endpoints_present:$STS"
  sleep 1
done
echo "fence_status=passed scope=home-postgres statefulset=$STS"
