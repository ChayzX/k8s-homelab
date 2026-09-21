#!/usr/bin/env bash
set -Eeuo pipefail

# Canada is a last-resort recovery site only. Home and Oracle are always
# preferred; this script exists for the exceptional case where both are
# already dark. It is manual and operator-confirmed -- there is no unattended
# controller for Canada (authority-gate.ps1's continuous supervisor task is
# deliberately left Disabled on the live host; only -Once is ever invoked
# here, per the design this preserves from an earlier, never-merged draft).
#
# No old-writer fence step runs here, unlike Oracle's promotion path: by the
# time both dual-gate checks below pass, Home and Oracle have already been
# independently, positively proven dark (zero ready replicas), so there is no
# live writer left to race against.
#
# Run from an operator workstation with:
#   - SSH to the Canada host (CANADA_ADMIN_KEY / BotAdmin@100.104.83.28)
#   - SSH + passwordless sudo kubectl to Home's node (minecraftmachine)
#   - SSH + passwordless sudo kubectl to Oracle's node (ubuntu@oracle)
# It does not need any credential installed on Canada itself.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: canada-last-resort-promote.sh --confirm | --dry-run

Required:
  CANADA_LAST_RESORT_CONFIRM     single-use operator acknowledgement token
  CANADA_ADDRESS                 Canada's Tailscale address (100.104.83.28)
  CANADA_ADMIN_KEY                path to the dedicated Canada admin SSH key

Optional overrides:
  CANADA_LAST_RESORT_HOME_GATE    command proving home is dark (default: canada-last-resort-home-gate.sh)
  CANADA_LAST_RESORT_ORACLE_GATE  command proving oracle is dark (default: canada-last-resort-oracle-gate.sh)
  CANADA_POSTGRES_CONTAINER       default pantrybot-canada-postgres
USAGE
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }
mode=${1:-}
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] || { usage >&2; exit 2; }

: "${CANADA_LAST_RESORT_CONFIRM:?CANADA_LAST_RESORT_CONFIRM is required: canada is last resort and requires explicit operator acknowledgement}"
: "${CANADA_ADDRESS:?CANADA_ADDRESS is required}"
: "${CANADA_ADMIN_KEY:?CANADA_ADMIN_KEY is required}"
CANADA_POSTGRES_CONTAINER="${CANADA_POSTGRES_CONTAINER:-pantrybot-canada-postgres}"
HOME_GATE="${CANADA_LAST_RESORT_HOME_GATE:-$SCRIPT_DIR/canada-last-resort-home-gate.sh}"
ORACLE_GATE="${CANADA_LAST_RESORT_ORACLE_GATE:-$SCRIPT_DIR/canada-last-resort-oracle-gate.sh}"

case "$CANADA_ADDRESS" in
  100.104.83.28) ;;
  *) echo 'refusing unexpected Canada identity' >&2; exit 1 ;;
esac

echo "canada_last_resort=checking home and oracle dark-site gates" >&2
if ! eval "$HOME_GATE"; then
  echo 'promotion failed: home is not confirmed dark' >&2
  exit 1
fi
if ! eval "$ORACLE_GATE"; then
  echo 'promotion failed: oracle is not confirmed dark' >&2
  exit 1
fi
echo 'canada_last_resort_guard=home_dark oracle_dark' >&2

ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -i "$CANADA_ADMIN_KEY" "BotAdmin@$CANADA_ADDRESS")

# Windows OpenSSH runs the remote command through cmd.exe, which does not
# preserve argv boundaries the way ssh's array-style invocation assumes on a
# POSIX remote shell: ssh flattens "${ssh_args[@]}" cmd arg1 arg2 'arg three'
# into one space-joined string, and single quotes are not special to cmd.exe.
# Found live 2026-09-21 mid-rehearsal: 'select pg_is_in_recovery();' arrived
# at psql as two bare words, "select" as the -c argument and
# "pg_is_in_recovery();" as an ignored extra positional argument. The fix is
# to build the entire remote command as ONE pre-quoted string ourselves,
# using double quotes (which cmd.exe does honor for grouping), and pass that
# single string as ssh's only trailing argument.
canada_psql() {
  ssh "${ssh_args[@]}" "docker exec $CANADA_POSTGRES_CONTAINER psql -U pantry -d pantry -tAc \"$1\""
}

if [[ "$mode" == "--dry-run" ]]; then
  ssh "${ssh_args[@]}" "docker inspect --format=\"{{.State.Status}}\" $CANADA_POSTGRES_CONTAINER" >/dev/null
  echo 'promotion_site=canada dry_run=true postgres_container_reachable=true'
  exit 0
fi

echo 'canada_last_resort=promoting local standby' >&2
recovery_before="$(canada_psql 'select pg_is_in_recovery();')"
case "$recovery_before" in
  t)
    canada_psql 'select pg_promote();' >/dev/null
    ;;
  f)
    echo 'canada_last_resort=already primary, resuming without re-promoting' >&2
    ;;
  *)
    echo "promotion failed: unexpected canada recovery state: $recovery_before" >&2
    exit 1
    ;;
esac

deadline=$((SECONDS + 60))
while (( SECONDS < deadline )); do
  role="$(canada_psql 'select pg_is_in_recovery();')"
  [[ "$role" == f ]] && break
  sleep 1
done
if [[ "${role:-}" != f ]]; then
  echo 'promotion failed: canada PostgreSQL did not leave recovery mode' >&2
  exit 1
fi
echo 'canada_last_resort=promoted verified' >&2

echo 'canada_last_resort=starting application containers via the existing authority gate (-Once)' >&2
ssh "${ssh_args[@]}" "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\ProgramData\\PantryBotCanadaPrep\\authority-gate.ps1 -Once"

echo 'canada_last_resort=verifying readiness' >&2
readiness="$(ssh "${ssh_args[@]}" "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\ProgramData\\PantryBotCanadaPrep\\check-canada-ready.ps1")"
echo "$readiness"
if grep -q ERROR <<<"$readiness"; then
  echo 'promotion failed: one or more canada services failed readiness after promotion' >&2
  exit 1
fi

echo 'canada_last_resort=promoted database=primary applications=started routing=not_yet_published'
echo 'Route publishing to Canada is a separate, deliberate step: see docs/recovery/runbooks/pantrybot.md and publish-cloudflare-routes.sh (requires the same CANADA_LAST_RESORT_CONFIRM token).'
