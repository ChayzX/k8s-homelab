#!/usr/bin/env bash
set -Eeuo pipefail

# Voluntary hand-back for a lower-priority ACTIVE k3s site (#191): never
# preemption - only the holder yields, and only to a healthy caught-up
# higher-priority standby.
#   handback.sh --check    exit 0 when the target standby has been streaming
#                          with <= HANDBACK_MAX_LAG_BYTES lag for
#                          HANDBACK_STABLE_SECONDS (state kept across calls)
#   handback.sh --execute  drain + yield; exit 0 yielded, 2 aborted (still
#                          primary, apps restored), other = error
# The promoter keeps the lease alive (LeaseGuard) while --execute runs and
# stops renewing after exit 0; the target's own promoter then takes over.
: "${HANDBACK_SITE:?}" "${HANDBACK_TARGET_SITE:?}" "${HANDBACK_TARGET_ADDR:?}" "${PANTRY_STANDBY_POD:?}"
NS="${PANTRY_NAMESPACE:-pantry-bot}"
STABLE="${HANDBACK_STABLE_SECONDS:-600}"
MAX_LAG="${HANDBACK_MAX_LAG_BYTES:-1048576}"
STATE_DIR="${HANDBACK_STATE_DIR:-/var/lib/pantry-postgres-promoter}"
STABLE_FILE="$STATE_DIR/handback-stable"
MARKER="$STATE_DIR/handback.json"
LIB="${FAILOVER_LIB:-/usr/local/lib/failover-witness}"
LOCAL_FENCE="${HANDBACK_LOCAL_FENCE:-$LIB/fence-${HANDBACK_SITE}-postgres-local.sh}"
[[ "$HANDBACK_SITE" == oracle ]] && LOCAL_FENCE="${HANDBACK_LOCAL_FENCE:-$LIB/fence-oracle-postgres-local.sh}"
WRITERS="${HANDBACK_WRITERS:-pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared}"
APP="pantry-${HANDBACK_TARGET_SITE}-standby"
MY_SLOT="pantry_${HANDBACK_SITE}_standby"
D=/var/lib/postgresql/data

sql() { kubectl -n "$NS" exec "$PANTRY_STANDBY_POD" -c postgres -- psql -U pantry -d pantry -AtX -v ON_ERROR_STOP=1 -c "$1"; }
say() { echo "handback site=$HANDBACK_SITE target=$HANDBACK_TARGET_SITE $*"; }

case "${1:-}" in
--check)
  [[ "$(sql 'select pg_is_in_recovery()')" == f ]] || { rm -f "$STABLE_FILE"; exit 1; }
  row="$(sql "select state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint from pg_stat_replication where application_name = '$APP' limit 1" || true)"
  state="${row%%|*}"; lag="${row##*|}"
  if [[ "$state" != streaming || ! "$lag" =~ ^[0-9]+$ ]] || (( lag > MAX_LAG )); then rm -f "$STABLE_FILE"; exit 1; fi
  now="$(date +%s)"; mkdir -p "$STATE_DIR"
  [[ -f "$STABLE_FILE" ]] || echo "$now" > "$STABLE_FILE"
  since="$(cat "$STABLE_FILE")"
  (( now - since >= STABLE )) || exit 1
  say "eligible stable_for=$((now - since))s lag=${lag}B"
  exit 0 ;;
--execute) ;;
*) echo "usage: handback.sh --check|--execute" >&2; exit 64 ;;
esac

restore_apps() {
  read -r -a ws <<<"$WRITERS"
  for w in "${ws[@]}"; do kubectl -n "$NS" scale deployment "$w" --replicas=1 >/dev/null 2>&1 || true; done
}

# 1. stop taking work (apps + connector), keep the DB
PANTRY_FENCE_APPS_ONLY=1 "$LOCAL_FENCE" --confirm >/dev/null
# 2. read-only, drop sessions, pin the final LSN
sql "ALTER SYSTEM SET default_transaction_read_only = 'on'" >/dev/null
sql "SELECT pg_reload_conf()" >/dev/null
sleep 2
sql "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database() AND backend_type = 'client backend'" >/dev/null
final="$(sql 'select pg_current_wal_flush_lsn()')"
# 3. the target must have replayed everything
caught=0
for _ in $(seq 1 120); do
  d="$(sql "select coalesce((select pg_wal_lsn_diff(replay_lsn, '$final') from pg_stat_replication where application_name = '$APP' limit 1), -1)")"
  if [[ "${d%%.*}" =~ ^[0-9]+$ ]]; then caught=1; break; fi
  sleep 0.5
done
if (( ! caught )); then
  sql "ALTER SYSTEM RESET default_transaction_read_only" >/dev/null; sql "SELECT pg_reload_conf()" >/dev/null
  restore_apps
  rm -f "$STABLE_FILE"
  say "aborted reason=target_not_caught_up final=$final"
  exit 2
fi
# 4. become a standby of the target on next start (slot first: not replicated)
host="${HANDBACK_TARGET_ADDR%:*}"; port="${HANDBACK_TARGET_ADDR##*:}"
kubectl -n "$NS" exec "$PANTRY_STANDBY_POD" -c postgres -- psql -X -d "host=$host port=$port user=pantry_replicator replication=true passfile=$D/.pgpass connect_timeout=5" -c "CREATE_REPLICATION_SLOT $MY_SLOT PHYSICAL RESERVE_WAL" >/dev/null 2>&1 || true
sql "ALTER SYSTEM SET primary_conninfo = 'user=pantry_replicator passfile=$D/.pgpass host=$host port=$port application_name=pantry-${HANDBACK_SITE}-standby'" >/dev/null
sql "ALTER SYSTEM SET primary_slot_name = '$MY_SLOT'" >/dev/null
sql "ALTER SYSTEM RESET default_transaction_read_only" >/dev/null
kubectl -n "$NS" exec "$PANTRY_STANDBY_POD" -c postgres -- su postgres -c "touch $D/standby.signal"
# 5. clean shutdown via the full fence (DB stays down until a primary exists:
#    standby-rejoin.sh restarts it then, or reclaims if none appears)
mkdir -p "$STATE_DIR"
printf '{"site": "%s", "target": "%s", "final_lsn": "%s", "yielded_at": %s}\n' "$HANDBACK_SITE" "$HANDBACK_TARGET_SITE" "$final" "$(date +%s)" > "$MARKER"
"$LOCAL_FENCE" --confirm >/dev/null
rm -f "$STABLE_FILE"
say "yielded final=$final"
exit 0
