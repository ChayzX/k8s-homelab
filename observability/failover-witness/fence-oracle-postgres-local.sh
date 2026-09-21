#!/usr/bin/env bash
set -Eeuo pipefail

# Fence only the Oracle-local standby-reseed PostgreSQL StatefulSet. This is the
# local writer-domain fence used before Oracle promotion; it never stops k3s
# or deletes the PVC.
KUBECTL_BIN="${ORACLE_KUBECTL:-kubectl}"
NS="${PANTRY_NAMESPACE:-pantry-bot}"
STS="${PANTRY_STANDBY_STATEFULSET:-postgres-authority-standby-home}"
TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-60}"

[[ "${1:-}" == "--confirm" && "$#" == 1 ]] || {
  echo "fence_status=failed reason=explicit_confirmation_required" >&2
  exit 2
}
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "fence_status=failed reason=invalid_timeout" >&2; exit 1; }
"$KUBECTL_BIN" version --request-timeout=5s >/dev/null 2>&1 || { echo "fence_status=failed reason=kubernetes_api_unavailable" >&2; exit 1; }
"$KUBECTL_BIN" -n "$NS" get statefulset "$STS" >/dev/null 2>&1 || { echo "fence_status=failed reason=no_local_postgres_statefulset" >&2; exit 1; }

"$KUBECTL_BIN" -n "$NS" scale statefulset "$STS" --replicas=0 >/dev/null
pod="${STS}-0"
if "$KUBECTL_BIN" -n "$NS" get pod "$pod" >/dev/null 2>&1; then
  "$KUBECTL_BIN" -n "$NS" delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null || true
fi
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  addresses="$("$KUBECTL_BIN" -n "$NS" get endpoints "$STS" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  [[ -z "$addresses" ]] && break
  (( $(date +%s) < deadline )) || { echo "fence_status=failed reason=endpoints_present:$STS" >&2; exit 1; }
  sleep 1
done
echo "fence_status=passed scope=oracle-local-postgres statefulset=$STS"
