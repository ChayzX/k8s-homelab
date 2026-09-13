#!/usr/bin/env bash
set -euo pipefail

promotion="scripts/pantrybot-promote-oracle.sh"
runbook="docs/recovery/runbooks/authentik.md"

usage() {
  printf 'Usage: %s [--promotion PATH] [--runbook PATH]\n' "${0##*/}"
}

while (($#)); do
  case "$1" in
    --promotion)
      (($# >= 2)) || { usage >&2; exit 2; }
      promotion=$2
      shift 2
      ;;
    --runbook)
      (($# >= 2)) || { usage >&2; exit 2; }
      runbook=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ -f "$promotion" ]] || { echo "missing promotion script: $promotion" >&2; exit 1; }
[[ -f "$runbook" ]] || { echo "missing Authentik runbook: $runbook" >&2; exit 1; }

require_runbook() {
  local requirement=$1
  grep -Fq "$requirement" "$runbook" || {
    echo "missing rollback requirement: $requirement" >&2
    exit 1
  }
}

line_number() {
  local needle=$1
  awk -v needle="$needle" 'index($0, needle) { print NR; exit }' "$promotion"
}

auth_fence_line=$(line_number '"${auth_home_fence_command[@]}" --confirm')
auth_promote_line=$(line_number 'pg_ctl -D /var/lib/postgresql/data promote')
auth_route_line=$(line_number 'auth_patch=')

[[ -n "$auth_fence_line" ]] || {
  echo 'promotion script has no explicit Authentik home fence' >&2
  exit 1
}
[[ -n "$auth_promote_line" ]] || {
  echo 'promotion script has no Authentik database promotion' >&2
  exit 1
}
[[ -n "$auth_route_line" ]] || {
  echo 'promotion script has no Authentik endpoint switch' >&2
  exit 1
}
(( auth_fence_line < auth_promote_line )) || {
  echo 'Authentik home fence must precede database promotion' >&2
  exit 1
}
(( auth_promote_line < auth_route_line )) || {
  echo 'Authentik endpoint switch must follow database promotion' >&2
  exit 1
}

require_runbook 'Fence the promoted writer'
require_runbook 're-seed home'
require_runbook 'promote home through the same controlled sequence'
require_runbook 'Never run both Authentik databases writable'
require_runbook 'old home writer rejects writes'

printf 'authentik_rollback_contract=passed\n'
