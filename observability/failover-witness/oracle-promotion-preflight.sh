#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only gate for an operator preparing a controlled Oracle promotion.
# This never acquires authority, fences, promotes, patches a Service, or
# scales workloads.
NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}"
POD="${PANTRY_STANDBY_POD:-postgres-authority-standby-home-0}"
CONTAINER="${PANTRY_POSTGRES_CONTAINER:-postgres}"
DB="${PANTRY_POSTGRES_DB:-pantry}"
USER_NAME="${PANTRY_POSTGRES_USER:-pantry}"
PORT="${PANTRY_POSTGRES_PORT:-5432}"
DATA_DIRECTORY="${PANTRY_POSTGRES_DATA_DIRECTORY:-/var/lib/postgresql/data}"
PROMOTER_UNIT="${PANTRY_PROMOTER_UNIT:-pantry-postgres-oracle-promoter.service}"

fail() {
  echo "promotion_preflight=failed reason=$1" >&2
  exit 1
}

kubectl_bin=(kubectl -n "$NAMESPACE")
"${kubectl_bin[@]}" get pod "$POD" >/dev/null 2>&1 || fail "standby_pod_missing"
ready="$("${kubectl_bin[@]}" get pod "$POD" -o jsonpath='{.status.containerStatuses[?(@.name=="postgres")].ready}')"
[[ "$ready" == true ]] || fail "standby_pod_not_ready"

psql=("${kubectl_bin[@]}" exec "$POD" -c "$CONTAINER" -- psql -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d "$DB" -Atqc)
recovery="$("${psql[@]}" 'select pg_is_in_recovery();')"
[[ "$recovery" == t ]] || fail "database_is_not_standby"
read_only="$("${psql[@]}" "select current_setting('transaction_read_only');")"
[[ "$read_only" == on ]] || fail "standby_is_not_read_only"
identity="$("${psql[@]}" 'select system_identifier from pg_control_system();')"
receive="$("${psql[@]}" "select coalesce(pg_last_wal_receive_lsn()::text, '');")"
replay="$("${psql[@]}" "select coalesce(pg_last_wal_replay_lsn()::text, '');")"
[[ -n "$identity" && -n "$receive" && -n "$replay" ]] || fail "missing_replication_evidence"

if systemctl is-active --quiet "$PROMOTER_UNIT"; then
  fail "promoter_is_active"
fi

printf 'promotion_preflight=passed recovery=true read_only=%s system_identifier=%s receive_lsn=%s replay_lsn=%s data_directory=%s promoter=inactive\n' \
  "$read_only" "$identity" "$receive" "$replay" "$DATA_DIRECTORY"
