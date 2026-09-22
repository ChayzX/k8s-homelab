#!/bin/sh
# PantryBot standby follower (#191). Runs beside a site's PostgreSQL (k8s
# sidecar sharing PGDATA + the unix socket, or a Canada companion container
# sharing the DB's network namespace and volume).
#
# Every INTERVAL seconds it records the local role and replication health in
# $STATE (read by promotion-gate.sh as the freshness heartbeat). When the local
# standby has not been streaming for REPOINT_AFTER seconds it looks for the
# new primary among PEERS and re-points primary_conninfo/primary_slot_name.
#
# Safety rules:
# - Never promotes, never writes data, never touches a primary.
# - Follows only a peer that answers pg_is_in_recovery()=f with the SAME
#   system identifier, and only if exactly one such peer is reachable; two
#   reachable primaries is logged as split brain and nothing is changed.
# - Peers are probed with the replication role (replication=database), whose
#   password comes from the passfile, never from argv or the environment.
set -eu

: "${SITE:?}" "${PEERS:?}"            # PEERS="home=100.84.89.87:5432 oracle=..."
D="${PGDATA:-/var/lib/postgresql/data}"
STATE="${STATE:-$D/pantry-follower.state}"
INTERVAL="${INTERVAL:-5}"
REPOINT_AFTER="${REPOINT_AFTER:-20}"
SLOT="${SLOT:-pantry_${SITE}_standby}"
APP_NAME="${APP_NAME:-pantry-${SITE}-standby}"
LOCAL="${LOCAL_CONNINFO:-user=pantry dbname=pantry}"
PASSFILE="$D/.pgpass"

log() { printf '%s follower site=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SITE" "$*" >&2; }
lq() { psql -X -At -d "$LOCAL" -c "$1" 2>/dev/null; }
peer_q() { # host port sql
  psql -X -At -d "host=$1 port=$2 user=pantry_replicator dbname=pantry replication=database connect_timeout=4 passfile=$PASSFILE" -c "$3" 2>/dev/null
}

state_get() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | head -1 || true; }
# Survive sidecar restarts: the disconnection clock must not reset.
disconnected_since=$(state_get disconnected_since)
last_streaming=$(state_get last_streaming)
repointed_at=$(state_get repointed_at)
topology_checked_at=$(state_get topology_checked_at)
pause() { [ "${ONESHOT:-0}" = 1 ] && exit 0; sleep "$INTERVAL"; }

write_state() { # role streaming upstream sysid timeline receive replay
  now=$(date +%s)
  tmp="$STATE.tmp"
  {
    echo "site=$SITE"
    echo "sampled_at=$now"
    echo "role=$1"
    echo "streaming=$2"
    echo "upstream=$3"
    echo "system_identifier=$4"
    echo "timeline=$5"
    echo "receive_lsn=$6"
    echo "replay_lsn=$7"
    echo "last_streaming=$last_streaming"
    echo "disconnected_since=$disconnected_since"
    echo "repointed_at=$repointed_at"
    echo "topology_checked_at=$topology_checked_at"
  } > "$tmp" && mv "$tmp" "$STATE"
}

find_primary() { # prints host:port of the unique same-sysid primary, or nothing
  sysid="$1"; found=""; count=0
  for entry in $PEERS; do
    hp="${entry#*=}"; host="${hp%:*}"; port="${hp##*:}"
    out=$(peer_q "$host" "$port" "select pg_is_in_recovery(), (select system_identifier from pg_control_system())") || continue
    rec="${out%%|*}"; psys="${out##*|}"
    if [ "$rec" = f ] && [ "$psys" = "$sysid" ]; then
      found="$hp"; count=$((count + 1))
    fi
  done
  if [ "$count" -gt 1 ]; then log "SPLIT_BRAIN_SUSPECTED primaries=$count; not following"; return 0; fi
  [ "$count" -eq 1 ] && printf '%s' "$found"
  return 0
}

repoint() { # host port
  host="$1"; port="$2"
  # Slots are not replicated; create ours on the new primary (exists = fine).
  psql -X -At -d "host=$host port=$port user=pantry_replicator replication=true connect_timeout=4 passfile=$PASSFILE" \
    -c "CREATE_REPLICATION_SLOT $SLOT PHYSICAL RESERVE_WAL" >/dev/null 2>&1 || true
  conninfo="user=pantry_replicator passfile=$PASSFILE host=$host port=$port application_name=$APP_NAME"
  lq "ALTER SYSTEM SET primary_conninfo = '$conninfo'" >/dev/null
  lq "ALTER SYSTEM SET primary_slot_name = '$SLOT'" >/dev/null
  lq "SELECT pg_reload_conf()" >/dev/null
  repointed_at=$(date +%s)
  log "repointed upstream=$host:$port slot=$SLOT"
}

[ "${ONESHOT:-0}" = 1 ] || log "started peers=\"$PEERS\" repoint_after=${REPOINT_AFTER}s"
while :; do
  row=$(lq "select pg_is_in_recovery(), (select system_identifier from pg_control_system()), (select timeline_id from pg_control_checkpoint())" || true)
  if [ -z "$row" ]; then
    write_state down 0 "" "" "" "" ""
    pause; continue
  fi
  rec=$(echo "$row" | cut -d'|' -f1); sysid=$(echo "$row" | cut -d'|' -f2); tli=$(echo "$row" | cut -d'|' -f3)
  if [ "$rec" = f ]; then
    disconnected_since=""
    write_state primary 0 "" "$sysid" "$tli" "" ""
    pause; continue
  fi
  wr=$(lq "select coalesce((select status||'|'||sender_host||':'||sender_port from pg_stat_wal_receiver),'none|'), coalesce(pg_last_wal_receive_lsn()::text,''), coalesce(pg_last_wal_replay_lsn()::text,'')" || true)
  status=$(echo "$wr" | cut -d'|' -f1); upstream=$(echo "$wr" | cut -d'|' -f2)
  recv=$(echo "$wr" | cut -d'|' -f3); replay=$(echo "$wr" | cut -d'|' -f4)
  now=$(date +%s)
  if [ "$status" = streaming ]; then
    last_streaming=$now; disconnected_since=""
    # Every 60s: streaming from a standby (cascade) while a unique primary
    # exists elsewhere -> follow the primary directly (e.g. after a hand-back).
    if [ -z "$topology_checked_at" ] || [ $((now - topology_checked_at)) -ge "${TOPOLOGY_CHECK_EVERY:-60}" ]; then
      topology_checked_at=$now
      target=$(find_primary "$sysid")
      if [ -n "$target" ] && [ "$target" != "$upstream" ]; then
        up_rec=$(peer_q "${upstream%:*}" "${upstream##*:}" "select pg_is_in_recovery()" || true)
        if [ "${up_rec%%|*}" = t ]; then
          log "cascade upstream=$upstream is a standby; primary=$target"
          repoint "${target%:*}" "${target##*:}"
        fi
      fi
    fi
    write_state standby 1 "$upstream" "$sysid" "$tli" "$recv" "$replay"
    pause; continue
  fi
  [ -n "$disconnected_since" ] || disconnected_since=$now
  write_state standby 0 "$upstream" "$sysid" "$tli" "$recv" "$replay"
  if [ $((now - disconnected_since)) -ge "$REPOINT_AFTER" ] && { [ -z "$repointed_at" ] || [ $((now - repointed_at)) -ge 60 ]; }; then
    target=$(find_primary "$sysid")
    if [ -n "$target" ]; then
      repoint "${target%:*}" "${target##*:}"
      # persist repointed_at now: ONESHOT runs exit before the next sample
      write_state standby 0 "$upstream" "$sysid" "$tli" "$recv" "$replay"
    fi
  fi
  pause
done
