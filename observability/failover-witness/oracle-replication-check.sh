#!/usr/bin/env bash
set -Eeuo pipefail

# Promotion gate: the local PostgreSQL must still be an intact standby.
# Override names only in the root-owned environment file; never infer a
# primary from a recent replay timestamp.
NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}"
POD="${PANTRY_STANDBY_POD:-postgres-authority-standby-0}"
CONTAINER="${PANTRY_POSTGRES_CONTAINER:-postgres}"
DB="${PANTRY_POSTGRES_DB:-pantrybot}"
USER_NAME="${PANTRY_POSTGRES_USER:-postgres}"
PORT="${PANTRY_POSTGRES_PORT:-5432}"
psql=(kubectl -n "$NAMESPACE" exec "$POD" -c "$CONTAINER" -- psql -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d "$DB" -Atqc)
recovery="$("${psql[@]}" 'select pg_is_in_recovery();')"
[[ "$recovery" == t ]] || { echo 'replication_check=failed reason=local_database_is_not_standby' >&2; exit 1; }
identity="$("${psql[@]}" 'select system_identifier from pg_control_system();')"
receive="$("${psql[@]}" "select coalesce(pg_last_wal_receive_lsn()::text,'');")"
replay="$("${psql[@]}" "select coalesce(pg_last_wal_replay_lsn()::text,'');")"
[[ -n "$identity" && -n "$receive" && -n "$replay" ]] || { echo 'replication_check=failed reason=missing_wal_evidence' >&2; exit 1; }
echo "replication_check=passed recovery=true system_identifier=$identity receive_lsn=$receive replay_lsn=$replay"
