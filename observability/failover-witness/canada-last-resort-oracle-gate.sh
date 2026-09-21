#!/usr/bin/env bash
set -Eeuo pipefail

# Positive, private, host-level proof that Oracle is dark before Canada
# could ever be considered as a last-resort promotion target. Mirrors
# canada-last-resort-home-gate.sh: exits 0 only when every known Oracle
# PostgreSQL StatefulSet in the namespace reports zero ready replicas.
# Unreachable fails closed, same reasoning as the Home gate.
ORACLE_SSH_HOST="${CANADA_LAST_RESORT_ORACLE_SSH_HOST:-ubuntu@100.78.181.15}"
ORACLE_SSH_IDENTITY="${CANADA_LAST_RESORT_ORACLE_SSH_IDENTITY:-}"
NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}"

ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
[[ -n "$ORACLE_SSH_IDENTITY" ]] && ssh_args+=(-i "$ORACLE_SSH_IDENTITY")

remote_check='
set -Eeuo pipefail
names=$(sudo -n kubectl -n '"$NAMESPACE"' get statefulset -o name 2>/dev/null | grep "statefulset.apps/postgres-authority" || true)
if [[ -z "$names" ]]; then
  echo "oracle_gate=failed reason=no_postgres_statefulset_found" >&2
  exit 1
fi
live=0
for name in $names; do
  ready=$(sudo -n kubectl -n '"$NAMESPACE"' get "$name" -o jsonpath="{.status.readyReplicas}" 2>/dev/null || echo 0)
  ready="${ready:-0}"
  if [[ "$ready" -gt 0 ]]; then
    echo "oracle_gate=failed reason=live_ready_replicas name=$name ready=$ready" >&2
    live=1
  fi
done
exit $live
'

if ! ssh "${ssh_args[@]}" "$ORACLE_SSH_HOST" "$remote_check"; then
  echo "canada_last_resort_oracle_gate=failed reason=oracle_not_confirmed_dark" >&2
  exit 1
fi
echo "canada_last_resort_oracle_gate=passed reason=all_oracle_postgres_statefulsets_zero_ready"
