#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$ROOT/fence-authentik-home-from-oracle.sh"
fail() { echo "authentik-home-fence-transport-test=failed reason=$1" >&2; exit 1; }

[[ -x "$SCRIPT" ]] || fail "script_not_executable"
grep -q '^HOME_HOST=".*100\.84\.89\.87' "$SCRIPT" || fail "home_identity_not_fixed"
grep -q 'fence-authentik-postgres-home.sh' "$SCRIPT" || fail "authentik_remote_target_missing"
! grep -q 'fence-pantry-postgres.sh' "$SCRIPT" || fail "pantry_target_present"
grep -q -- '--dry-run' "$SCRIPT" || fail "dry_run_missing"
grep -q 'StrictHostKeyChecking=yes' "$SCRIPT" || fail "host_key_check_missing"
grep -q 'UserKnownHostsFile' "$SCRIPT" || fail "known_hosts_missing"
grep -q 'BatchMode=yes' "$SCRIPT" || fail "batch_mode_missing"
echo 'authentik-home-fence-transport-test=passed'
