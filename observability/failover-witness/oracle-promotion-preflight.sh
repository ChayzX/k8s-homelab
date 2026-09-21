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
HOME_FENCER_KUBECONFIG="${HOME_FENCER_KUBECONFIG:-/etc/failover-witness/pantry-postgres-fencer-home.kubeconfig}"
HOME_KUBECTL="${HOME_KUBECTL:-kubectl}"
PANTRY_HOME_STATEFULSET="${PANTRY_HOME_STATEFULSET:-postgres-authority-home-v2}"

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

# RBAC preflight: the scoped Home fence must actually be authorized before a
# real promotion ever calls it. 2026-09-21: this exact grant silently
# drifted out of sync with a StatefulSet rename and only surfaced as a
# Forbidden error deep inside a live promotion attempt (previously, before
# the rename, the same drift was a silent FALSE POSITIVE — the fence
# "succeeded" against an already-decommissioned target while the real
# writer stayed live). Checking it here, read-only, catches that class of
# drift before it can ever reach production.
[[ -r "$HOME_FENCER_KUBECONFIG" ]] || fail "home_fencer_kubeconfig_unreadable"
rbac_pod="${PANTRY_HOME_STATEFULSET}-0"
"$HOME_KUBECTL" --kubeconfig="$HOME_FENCER_KUBECONFIG" -n "$NAMESPACE" auth can-i patch "statefulset/$PANTRY_HOME_STATEFULSET" >/dev/null 2>&1 \
  || fail "home_fencer_rbac_denied:statefulset_patch:$PANTRY_HOME_STATEFULSET"
"$HOME_KUBECTL" --kubeconfig="$HOME_FENCER_KUBECONFIG" -n "$NAMESPACE" auth can-i delete "pod/$rbac_pod" >/dev/null 2>&1 \
  || fail "home_fencer_rbac_denied:pod_delete:$rbac_pod"
"$HOME_KUBECTL" --kubeconfig="$HOME_FENCER_KUBECONFIG" -n "$NAMESPACE" auth can-i get "endpoints/$PANTRY_HOME_STATEFULSET" >/dev/null 2>&1 \
  || fail "home_fencer_rbac_denied:endpoints_get:$PANTRY_HOME_STATEFULSET"

if systemctl is-active --quiet "$PROMOTER_UNIT"; then
  fail "promoter_is_active"
fi

printf 'promotion_preflight=passed recovery=true read_only=%s system_identifier=%s receive_lsn=%s replay_lsn=%s data_directory=%s home_fencer_rbac=verified promoter=inactive\n' \
  "$read_only" "$identity" "$receive" "$replay" "$DATA_DIRECTORY"
