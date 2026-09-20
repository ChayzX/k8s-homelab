#!/usr/bin/env bash
set -Eeuo pipefail

# Post-activation check for the live Oracle standby-reseed topology. This is
# intentionally fail-closed: it proves the promoted database is writable,
# the manual authority Endpoints object has an address, and every mutating
# PantryBot deployment has reached its desired replica count.
POD_NAMESPACE="${PANTRY_ORACLE_POD_NAMESPACE:-pantry-bot}"
POD="${PANTRY_ORACLE_POD:-postgres-authority-standby-reseed-v2-0}"
SERVICE_NAMESPACE="${PANTRY_ORACLE_SERVICE_NAMESPACE:-pantry-bot}"
SERVICE="${PANTRY_ORACLE_SERVICE:-postgres-authority-standby-reseed-v2}"
POSTGRES_PORT="${PANTRY_ORACLE_POSTGRES_PORT:-5432}"
KUBECTL="${PANTRY_KUBECTL:-kubectl}"

fail() { echo "oracle_service_check=failed reason=$1" >&2; exit 1; }

command -v "$KUBECTL" >/dev/null || fail kubectl_unavailable
[[ "$POSTGRES_PORT" =~ ^[0-9]+$ ]] || fail invalid_postgres_port
recovery="$($KUBECTL -n "$POD_NAMESPACE" exec "$POD" -c postgres -- sh -ec "pg_isready -p '$POSTGRES_PORT' -U pantry -d pantry >/dev/null; psql -p '$POSTGRES_PORT' -U pantry -d pantry -Atc 'select pg_is_in_recovery();'")" || fail database_probe_failed
[[ "$recovery" == "f" ]] || fail database_not_primary
readonly="$($KUBECTL -n "$POD_NAMESPACE" exec "$POD" -c postgres -- sh -ec "psql -p '$POSTGRES_PORT' -U pantry -d pantry -Atc 'show transaction_read_only;'")" || fail readonly_probe_failed
[[ "$readonly" == "off" ]] || fail database_read_only
addresses="$($KUBECTL -n "$SERVICE_NAMESPACE" get endpoints "$SERVICE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
[[ -n "$addresses" ]] || fail authority_endpoint_missing

for deployment in pantry-commands-site pantry-private-api pantry-private-site pantry-overlay-delivery pantry-twitch-gateway pantry-chat-worker pantry-twitch-dispatcher; do
  replicas="$($KUBECTL -n "$SERVICE_NAMESPACE" get deployment "$deployment" -o jsonpath='{.spec.replicas}')" || fail "deployment_missing:$deployment"
  ready="$($KUBECTL -n "$SERVICE_NAMESPACE" get deployment "$deployment" -o jsonpath='{.status.readyReplicas}')"
  [[ "$replicas" =~ ^[1-9][0-9]*$ && "$ready" == "$replicas" ]] || fail "deployment_not_ready:$deployment"
done
echo "oracle_service_check=passed database=primary endpoint=present deployments=ready"
