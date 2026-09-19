#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SCRIPT="$ROOT/observability/failover-witness/fence-authentik-postgres-oracle.sh"
fail() { echo "authentik-oracle-fence-contract-test=failed reason=$1" >&2; exit 1; }
[[ -x "$SCRIPT" ]] || fail "script_not_executable"
grep -q '^NAMESPACE="auth"$' "$SCRIPT" || fail "namespace_not_fixed"
grep -q '^STATEFULSET="auth-postgresql-standby"$' "$SCRIPT" || fail "statefulset_not_fixed"
grep -q '^SERVICE="auth-postgresql-standby"$' "$SCRIPT" || fail "service_not_fixed"
! grep -q -i pantry "$SCRIPT" || fail "pantry_reference_present"
grep -q -- '--dry-run' "$SCRIPT" || fail "dry_run_missing"
grep -q -- '--replicas=0' "$SCRIPT" || fail "scale_fence_missing"
grep -q 'get endpoints "\$SERVICE"' "$SCRIPT" || fail "endpoint_check_missing"
echo 'authentik-oracle-fence-contract-test=passed'
