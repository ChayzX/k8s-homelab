#!/usr/bin/env bash
set -Eeuo pipefail

# Conservative automatic-failover supervisor for the promoting site. The probe
# must be a private, site-specific home-primary endpoint; public routes are
# invalid here because active-active Cloudflare routing can remain healthy.
# This supervisor never fences or promotes directly: the one-shot site-neutral
# promotion adapter remains the only mutation path. Authentik is not part of
# automatic failover (single-writer home model); only PantryBot may auto-promote.

usage() {
  cat <<'USAGE'
Usage: pantrybot-auto-failover-oracle.sh --confirm | --dry-run

Required environment for --confirm:
  HOME_PRIMARY_PROBE_URL        private home-primary readiness URL (optional)
  HOME_PRIMARY_PROBE_COMMAND     private argv probe that exits 0 only when home
                                PostgreSQL is primary (optional)
  ORACLE_PRIMARY_PROBE_COMMAND   private argv probe that exits 0 only when
                                the local site is already PostgreSQL primary
  HOME_FAILURE_THRESHOLD        consecutive failed probes before promotion
  HOME_PROBE_INTERVAL_SECONDS   delay between failed probes
  PROMOTION_COMMAND              exact one-shot promotion command including --confirm
                                invoking scripts/pantrybot-promote-site.sh

The promotion adapter also needs WITNESS_URL, WITNESS_SHARED_SECRET, and
OLD_WRITER_FENCE_COMMAND in its environment (root-owned EnvironmentFile).
USAGE
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }
mode=${1:-}
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] || { usage >&2; exit 2; }

: "${HOME_PRIMARY_PROBE_URL:=}"
: "${HOME_PRIMARY_PROBE_COMMAND:=}"
: "${ORACLE_PRIMARY_PROBE_COMMAND:?ORACLE_PRIMARY_PROBE_COMMAND is required}"
: "${HOME_FAILURE_THRESHOLD:?HOME_FAILURE_THRESHOLD is required}"
: "${HOME_PROBE_INTERVAL_SECONDS:?HOME_PROBE_INTERVAL_SECONDS is required}"
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
read -r -a oracle_primary_probe_command <<< "$ORACLE_PRIMARY_PROBE_COMMAND"
(( ${#oracle_primary_probe_command[@]} > 0 )) || {
  echo 'automatic failover refused: Oracle primary probe command is empty' >&2
  exit 1
}
read -r -a promotion_command <<< "$PROMOTION_COMMAND"
(( ${#promotion_command[@]} > 1 )) || {
  echo 'automatic failover refused: promotion command is empty' >&2
  exit 1
}
promotion_has_confirm=false
promotion_has_adapter=false
for arg in "${promotion_command[@]}"; do
  [[ "$arg" == --confirm ]] && promotion_has_confirm=true
  [[ "$arg" == *pantrybot-promote-site* ]] && promotion_has_adapter=true
done
[[ "$promotion_has_confirm" == true && "$promotion_has_adapter" == true ]] || {
  echo 'automatic failover refused: promotion command must be pantrybot-promote-site with --confirm' >&2
  exit 1
}

if [[ "$mode" == "--dry-run" ]]; then
  probe_source=private-home-primary-url
  [[ -n "$HOME_PRIMARY_PROBE_COMMAND" ]] && probe_source=private-home-primary-command
  printf '%s\n' \
    "probe_source=$probe_source" \
    oracle_primary_probe_validated \
    "failure_threshold=$HOME_FAILURE_THRESHOLD" \
    promotion_command_validated \
    automatic_promotion=disabled
  exit 0
fi

lock_path=${AUTO_FAILOVER_LOCK_PATH:-/run/lock/pantrybot-auto-failover-oracle.lock}
exec 9>"$lock_path"
flock -n 9 || { echo 'automatic failover skipped: another supervisor is running' >&2; exit 0; }

# Oracle may already be the authority while the home target is deliberately
# fenced or offline. Check the local authority first so expected home-target
# failures do not fill the journal or alert pipeline every timer tick.
if "${oracle_primary_probe_command[@]}" >/dev/null 2>&1; then
  echo oracle_already_primary
  exit 0
fi

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
# The one-shot adapter is site-neutral; it reads its own copy of the witness
# and old-writer-fence credentials from the root-owned EnvironmentFile.
exec "${promotion_command[@]}"
