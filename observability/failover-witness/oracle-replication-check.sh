#!/usr/bin/env bash
set -Eeuo pipefail

# Promotion gate: the local PostgreSQL must still be an intact standby.
# Override names only in the root-owned environment file; never infer a
# primary from a recent replay timestamp.
NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}"
POD="${PANTRY_STANDBY_POD:-postgres-authority-standby-home-0}"
CONTAINER="${PANTRY_POSTGRES_CONTAINER:-postgres}"
DB="${PANTRY_POSTGRES_DB:-pantry}"
USER_NAME="${PANTRY_POSTGRES_USER:-pantry}"
PORT="${PANTRY_POSTGRES_PORT:-5432}"
psql=(kubectl -n "$NAMESPACE" exec "$POD" -c "$CONTAINER" -- psql -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d "$DB" -Atqc)
recovery="$("${psql[@]}" 'select pg_is_in_recovery();')"
[[ "$recovery" == t ]] || { echo 'replication_check=failed reason=local_database_is_not_standby' >&2; exit 1; }
identity="$("${psql[@]}" 'select system_identifier from pg_control_system();')"
receive="$("${psql[@]}" "select coalesce(pg_last_wal_receive_lsn()::text,'');")"
replay="$("${psql[@]}" "select coalesce(pg_last_wal_replay_lsn()::text,'');")"
[[ -n "$identity" && -n "$receive" && -n "$replay" ]] || { echo 'replication_check=failed reason=missing_wal_evidence' >&2; exit 1; }

# This runs after fence_old_writer has already scaled the old writer's
# StatefulSet to zero and confirmed its k8s Service endpoints are empty —
# but that's a network/orchestration-level signal, not direct proof that
# the PostgreSQL replication protocol connection itself was torn down. This
# standby's own wal receiver process (pg_stat_wal_receiver has at most one
# row: this connection) should no longer be actively streaming once the old
# writer is genuinely gone; a receiver still reporting 'streaming' here
# means something is still serving WAL despite the k8s-level fence looking
# clean.
receiver_status="$("${psql[@]}" "select coalesce((select status from pg_stat_wal_receiver), 'none');")"
if [[ "$receiver_status" == "streaming" ]]; then
  echo "replication_check=failed reason=wal_receiver_still_streaming_after_old_writer_fence" >&2
  exit 1
fi
echo "replication_check=passed recovery=true system_identifier=$identity receive_lsn=$receive replay_lsn=$replay wal_receiver_status=$receiver_status"
