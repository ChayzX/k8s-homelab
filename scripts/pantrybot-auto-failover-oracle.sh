#!/usr/bin/env bash
set -Eeuo pipefail

# Conservative Oracle-side automatic-failover supervisor. The probe must be a
# private, site-specific home-primary endpoint; public routes are invalid here
# because active-active Cloudflare routing can remain healthy through Oracle.
# This supervisor never fences or promotes directly: the coupled promotion
# adapter remains the only mutation path.

usage() {
  cat <<'USAGE'
Usage: pantrybot-auto-failover-oracle.sh --confirm | --dry-run

Required environment for --confirm:
  HOME_PRIMARY_PROBE_URL        private home-primary readiness URL (optional)
  HOME_PRIMARY_PROBE_COMMAND     private argv probe that exits 0 only when home
                                PostgreSQL is primary (optional)
  HOME_FAILURE_THRESHOLD        consecutive failed probes before promotion
  HOME_PROBE_INTERVAL_SECONDS   delay between failed probes
  HOME_FENCE_COMMAND             PantryBot home fence adapter command
  AUTH_HOME_FENCE_COMMAND        Authentik home fence adapter command
  PROMOTION_COMMAND              exact promotion command including --confirm

The supervisor should run only on Oracle. It is deliberately fail-closed when
the home probe is reachable or the promotion contract is not exact.
USAGE
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }
mode=${1:-}
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] || { usage >&2; exit 2; }

: "${HOME_PRIMARY_PROBE_URL:=}"
: "${HOME_PRIMARY_PROBE_COMMAND:=}"
: "${HOME_FAILURE_THRESHOLD:?HOME_FAILURE_THRESHOLD is required}"
: "${HOME_PROBE_INTERVAL_SECONDS:?HOME_PROBE_INTERVAL_SECONDS is required}"
: "${HOME_FENCE_COMMAND:?HOME_FENCE_COMMAND is required}"
: "${AUTH_HOME_FENCE_COMMAND:?AUTH_HOME_FENCE_COMMAND is required}"
: "${PROMOTION_COMMAND:?PROMOTION_COMMAND is required}"

[[ -n "$HOME_PRIMARY_PROBE_URL" || -n "$HOME_PRIMARY_PROBE_COMMAND" ]] || {
  echo 'automatic failover refused: a private home-primary probe is required' >&2
  exit 1
}
[[ -z "$HOME_PRIMARY_PROBE_URL" || "$HOME_PRIMARY_PROBE_URL" != *://*public* ]] || {
  echo 'automatic failover refused: public probe is not site-specific' >&2
  exit 1
}
[[ "$HOME_FAILURE_THRESHOLD" =~ ^[1-9][0-9]*$ ]] || {
  echo 'automatic failover refused: invalid failure threshold' >&2
  exit 1
}
[[ "$HOME_PROBE_INTERVAL_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo 'automatic failover refused: invalid probe interval' >&2
  exit 1
}

if [[ -n "$HOME_PRIMARY_PROBE_COMMAND" ]]; then
  read -r -a probe_command <<< "$HOME_PRIMARY_PROBE_COMMAND"
  (( ${#probe_command[@]} > 0 )) || {
    echo 'automatic failover refused: home probe command is empty' >&2
    exit 1
  }
fi
read -r -a promotion_command <<< "$PROMOTION_COMMAND"
(( ${#promotion_command[@]} > 1 )) || {
  echo 'automatic failover refused: promotion command is empty' >&2
  exit 1
}
promotion_has_confirm=false
promotion_has_adapter=false
for arg in "${promotion_command[@]}"; do
  [[ "$arg" == --confirm ]] && promotion_has_confirm=true
  [[ "$arg" == *pantrybot-promote-oracle* ]] && promotion_has_adapter=true
done
[[ "$promotion_has_confirm" == true && "$promotion_has_adapter" == true ]] || {
  echo 'automatic failover refused: promotion command must be the Oracle adapter with --confirm' >&2
  exit 1
}

if [[ "$mode" == "--dry-run" ]]; then
  probe_source=private-home-primary-url
  [[ -n "$HOME_PRIMARY_PROBE_COMMAND" ]] && probe_source=private-home-primary-command
  printf '%s\n' \
    "probe_source=$probe_source" \
    "failure_threshold=$HOME_FAILURE_THRESHOLD" \
    promotion_command_validated \
    automatic_promotion=disabled
  exit 0
fi

lock_path=${AUTO_FAILOVER_LOCK_PATH:-/run/lock/pantrybot-auto-failover-oracle.lock}
exec 9>"$lock_path"
flock -n 9 || { echo 'automatic failover skipped: another supervisor is running' >&2; exit 0; }

for attempt in $(seq 1 "$HOME_FAILURE_THRESHOLD"); do
  probe_status=0
  if [[ -n "$HOME_PRIMARY_PROBE_COMMAND" ]]; then
    "${probe_command[@]}" >/dev/null 2>&1 || probe_status=$?
  else
    curl --fail --silent --show-error --max-time "${HOME_PROBE_TIMEOUT_SECONDS:-5}" \
      "$HOME_PRIMARY_PROBE_URL" >/dev/null || probe_status=$?
  fi
  if [[ "$probe_status" -eq 0 ]]; then
    echo "home_probe=healthy attempt=$attempt"
    exit 0
  fi
  echo "home_probe=failed attempt=$attempt" >&2
  [[ "$attempt" == "$HOME_FAILURE_THRESHOLD" ]] || sleep "$HOME_PROBE_INTERVAL_SECONDS"
done

echo home_probe=failed threshold_reached
export HOME_FENCE_COMMAND AUTH_HOME_FENCE_COMMAND
exec "${promotion_command[@]}"
