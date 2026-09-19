#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SCRIPT="$ROOT/observability/failover-witness/fence-authentik-postgres-home.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "authentik-home-fence-contract-test=failed reason=$1" >&2; exit 1; }

[[ -x "$SCRIPT" ]] || fail "fence_script_not_executable"
grep -q '^NAMESPACE="auth"$' "$SCRIPT" || fail "namespace_not_fixed"
grep -q '^STATEFULSET="auth-postgresql-home-primary"$' "$SCRIPT" || fail "statefulset_not_fixed"
grep -q '^SERVICE="auth-postgresql-home-primary"$' "$SCRIPT" || fail "service_not_fixed"
! grep -q -i pantry "$SCRIPT" || fail "pantry_reference_present"
grep -q -- '--dry-run' "$SCRIPT" || fail "dry_run_missing"
grep -q -- '--replicas=0' "$SCRIPT" || fail "scale_fence_missing"
grep -q 'get endpoints "\$SERVICE"' "$SCRIPT" || fail "endpoint_check_missing"
! grep -q -E 'delete (pvc|secret)|stop k3s|kubectl.*delete.*statefulset' "$SCRIPT" || fail "destructive_scope_expanded"

FAKE="$TMP/kubectl"
LOG="$TMP/kubectl.log"
cat > "$FAKE" <<'FAKE'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$FAKE_LOG"
if [[ "$1" == "version" ]]; then exit 0; fi
if [[ "$1" == "-n" && "$3" == "get" && "$4" == "statefulset" ]]; then exit 0; fi
if [[ "$1" == "-n" && "$3" == "get" && "$4" == "service" ]]; then
  printf '%s' '{"app.kubernetes.io/name":"auth-postgresql-home-primary"}'
  exit 0
fi
echo "unexpected fake kubectl call: $*" >&2
exit 1
FAKE
chmod +x "$FAKE"

FAKE_LOG="$LOG" AUTHENTIK_KUBECTL="$FAKE" "$SCRIPT" --dry-run >"$TMP/out"
grep -qx 'fence_status=dry-run scope=home-authentik-postgres namespace=auth statefulset=auth-postgresql-home-primary service=auth-postgresql-home-primary' "$TMP/out" || fail "dry_run_marker_missing"
! grep -q -E 'scale|delete|patch|apply' "$LOG" || fail "dry_run_mutated"

if AUTHENTIK_KUBECTL="$FAKE" "$SCRIPT" >/dev/null 2>"$TMP/error"; then
  fail "implicit_confirmation_allowed"
fi
grep -q 'explicit_confirmation_required' "$TMP/error" || fail "confirmation_error_missing"

echo 'authentik-home-fence-contract-test=passed'
