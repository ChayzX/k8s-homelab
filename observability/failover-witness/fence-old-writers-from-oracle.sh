#!/usr/bin/env bash
set -Eeuo pipefail

# Composite old-writer fence used before promotion. Each other site must be
# positively fenced when reachable; any failure from a reachable site is a
# hard failure. A site whose fence script reports exit 75 (network-level
# unreachable at its first probe - never refused/auth/mid-fence errors) is
# accepted as fenced by witness-lease expiry: by the time this runs the
# promoter already holds the lease, so the old holder's lease has expired and
# its lease guard has self-fenced. The grace wait covers a slow self-fence.
# Owner-approved policy, 2026-09-21: without this, a fully dark site blocks
# every automatic failover.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || {
  echo 'old_writer_fence=failed reason=explicit_confirmation_required' >&2
  exit 2
}

script_for() {
  case "$1" in
    home) echo "$SCRIPT_DIR/fence-home-direct-from-oracle.sh" ;;
    canada) echo "$SCRIPT_DIR/fence-canada-from-oracle.sh" ;;
  esac
}

# Phase 1: probe every site's fence transport before fencing any of them.
# Sites are fenced in order, so a failure on the last one leaves the earlier
# ones fenced with no way to undo it - and the promoter's restart loop then
# repeats that on every pass, re-fencing a healthy site indefinitely while
# never completing a promotion (#387, the 2026-09-23 outage). --dry-run
# verifies each transport without changing anything, so the common failure -
# a fence script that is broken, misconfigured, or hits an unusable remote -
# costs nothing instead of taking the cluster down. Exit 75 (network-level
# unreachable) is a valid probe outcome: that site is accepted as fenced by
# lease expiry below.
if [[ "$mode" == "--confirm" ]]; then
  for site in home canada; do
    set +e
    "$(script_for "$site")" --dry-run >/dev/null 2>&1
    rc=$?
    set -e
    case "$rc" in
      0 | 75) ;;
      *) echo "old_writer_fence=failed phase=probe site=$site rc=$rc (nothing fenced)" >&2; exit "$rc" ;;
    esac
  done
fi

results=()
unreachable=0
for site in home canada; do
  script="$(script_for "$site")"
  set +e
  "$script" "$mode"
  rc=$?
  set -e
  case "$rc" in
    0) results+=("$site=verified") ;;
    75) results+=("$site=lease_expiry"); unreachable=1 ;;
    *) echo "old_writer_fence=failed site=$site rc=$rc" >&2; exit "$rc" ;;
  esac
done

if (( unreachable )) && [[ "$mode" == "--confirm" ]]; then
  sleep "${PANTRY_UNREACHABLE_GRACE_SECONDS:-20}"
fi
echo "old_writer_fence=verified ${results[*]}"
