#!/usr/bin/env bash
set -Eeuo pipefail

# Composite old-writer fence used before HOME promotion (fences Oracle + Canada). Each other site must be
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

results=()
unreachable=0
for site in oracle canada; do
  case "$site" in
    oracle) script="$SCRIPT_DIR/fence-oracle-direct.sh" ;;
    canada) script="$SCRIPT_DIR/fence-canada-from-oracle.sh" ;;
  esac
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
