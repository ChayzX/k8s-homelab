#!/usr/bin/env bash
set -euo pipefail

# Authentik rollback contract gate.
#
# Under the single-Authentik-writer model (issue #198), the site-neutral
# PantryBot promotion adapter explicitly does NOT touch Authentik. Authentik
# promotion/failback is a manual, runbook-gated operation on home. This check
# guards the runbook-written safety gates and the adapter/runbook boundary:

promotion="scripts/pantrybot-promote-site.sh"
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

# The Auto-promote adapter must never fence or promote Authentik: home is the
# only Authentik writer and promotion of its database is a manual decision.
if grep -Eq 'auth_|authentik_|auth-postgresql' "$promotion"; then
  echo 'promotion script must not touch Authentik (single home writer)' >&2
  exit 1
fi

require_runbook 'Fence the promoted writer'
require_runbook 're-seed home'
require_runbook 'promote home through the same controlled sequence'
require_runbook 'Never run both Authentik databases writable'
require_runbook 'old home writer rejects writes'

printf 'authentik_rollback_contract=passed\n'