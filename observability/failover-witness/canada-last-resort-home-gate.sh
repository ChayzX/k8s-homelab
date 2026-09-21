#!/usr/bin/env bash
set -Eeuo pipefail

# Positive, private, host-level proof that Home is dark before Canada could
# ever be considered as a last-resort promotion target. Exits 0 only when
# every known Home PostgreSQL StatefulSet in the namespace reports zero
# ready replicas. Unreachable is NOT treated as dark: if this cannot reach
# Home's cluster at all, it fails closed (non-zero exit), because an
# unreachable site is not proof of a dead site -- the same partition could
# put a live writer on the far side of the same network split that makes it
# unreachable from here. A stale Terminating pod that still carries an old
# "primary" label is deliberately not trusted either (found live 2026-09-21:
# postgres-authority-home-failback-0 sat Terminating for 12h on a NotReady
# node while still labeled pantrybot.postgres/role=primary); only the
# StatefulSet's own readyReplicas count is trusted.
HOME_SSH_HOST="${CANADA_LAST_RESORT_HOME_SSH_HOST:-minecraftmachine}"
NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}"

remote_check='
set -Eeuo pipefail
names=$(sudo -n kubectl -n '"$NAMESPACE"' get statefulset -o name 2>/dev/null | grep "statefulset.apps/postgres-authority" || true)
if [[ -z "$names" ]]; then
  echo "home_gate=failed reason=no_postgres_statefulset_found" >&2
  exit 1
fi
live=0
for name in $names; do
  ready=$(sudo -n kubectl -n '"$NAMESPACE"' get "$name" -o jsonpath="{.status.readyReplicas}" 2>/dev/null || echo 0)
  ready="${ready:-0}"
  if [[ "$ready" -gt 0 ]]; then
    echo "home_gate=failed reason=live_ready_replicas name=$name ready=$ready" >&2
    live=1
  fi
done
exit $live
'

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOME_SSH_HOST" "$remote_check"; then
  echo "canada_last_resort_home_gate=failed reason=home_not_confirmed_dark" >&2
  exit 1
fi
echo "canada_last_resort_home_gate=passed reason=all_home_postgres_statefulsets_zero_ready"
