#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="$ROOT/scripts/authentik-restore-contract-check.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cat > "$TMP/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"get namespace restore-auth"* ]]; then
  printf '{}\n'
  exit 0
fi
if [[ "$*" == *"get namespace auth"* ]]; then
  printf '{}\n'
  exit 0
fi
if [[ "$*" == *"get statefulset auth-postgresql"* ]]; then
  printf '{"status":{"readyReplicas":1,"replicas":1}}\n'
  exit 0
fi
if [[ "$*" == *"get deployment auth-authentik-server"* || "$*" == *"get deployment auth-authentik-worker"* || "$*" == *"get deployment ldap-outpost"* ]]; then
  printf '{"status":{"readyReplicas":1,"replicas":1}}\n'
  exit 0
fi
if [[ "$*" == *"get secret"* ]]; then
  secret="$5"
  case "$secret" in
    auth-authentik)
      keys='AUTHENTIK_BOOTSTRAP_EMAIL AUTHENTIK_BOOTSTRAP_PASSWORD AUTHENTIK_BOOTSTRAP_TOKEN AUTHENTIK_EXTERNAL_HOST AUTHENTIK_POSTGRESQL__HOST AUTHENTIK_POSTGRESQL__NAME AUTHENTIK_POSTGRESQL__PASSWORD AUTHENTIK_POSTGRESQL__PORT AUTHENTIK_POSTGRESQL__USER AUTHENTIK_SECRET_KEY'
      if [[ "${AUTHENTIK_TEST_MISSING_KEY:-}" == 1 ]]; then
        keys="${keys% AUTHENTIK_SECRET_KEY}"
      fi
      ;;
    auth-postgresql)
      keys='password postgres-password'
      ;;
    ldap-outpost-token)
      keys='AUTHENTIK_TOKEN'
      ;;
    ldap-bind-service-account)
      keys='BIND_DN BIND_PASSWORD'
      ;;
    ldap-chase-initial-password)
      keys='PASSWORD'
      ;;
    *)
      printf 'unknown secret\n' >&2
      exit 1
      ;;
  esac
  jq -n --arg keys "$keys" '{data: ($keys | split(" ") | map({key: ., value: "c2FuaXRpemVk"}) | from_entries)}'
  exit 0
fi
printf 'unexpected kubectl call: %s\n' "$*" >&2
exit 1
EOF
chmod +x "$TMP/kubectl"

PATH="$TMP:$PATH" AUTHENTIK_RESTORE_NAMESPACE=restore-auth \
  "$CHECKER" > "$TMP/pass.out" || fail "complete isolated contract was rejected"
grep -qx 'authentik_restore_contract=passed' "$TMP/pass.out" || fail "missing pass marker"
if grep -Eq 'sanitized|c2FuaXRpemVk' "$TMP/pass.out"; then
  fail "checker output exposed secret-shaped data"
fi

if PATH="$TMP:$PATH" AUTHENTIK_RESTORE_NAMESPACE=auth "$CHECKER" > "$TMP/prod.out" 2>&1; then
  fail "production namespace was accepted"
fi
grep -q 'refusing production namespace' "$TMP/prod.out" || fail "production refusal was not explicit"

if PATH="$TMP:$PATH" AUTHENTIK_TEST_MISSING_KEY=1 AUTHENTIK_RESTORE_NAMESPACE=restore-auth \
  "$CHECKER" > "$TMP/missing.out" 2>&1; then
  fail "incomplete Secret contract was accepted"
fi
grep -q 'missing key AUTHENTIK_SECRET_KEY' "$TMP/missing.out" || fail "missing key failure was not explicit"

echo 'authentik-restore-contract-test=passed'
