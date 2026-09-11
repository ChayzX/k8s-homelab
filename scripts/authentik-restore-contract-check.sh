#!/usr/bin/env bash
set -euo pipefail

# Names-only validation for an isolated Authentik/PostgreSQL restore.
#
# This deliberately does not read or print Secret values. It proves only that
# the disposable target contains the runtime objects and secret key names
# needed for a follow-up provider/session test. It is not a production health
# check and refuses the live auth namespace.

AUTHENTIK_RESTORE_NAMESPACE="${AUTHENTIK_RESTORE_NAMESPACE:-}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"

if [[ -z "$AUTHENTIK_RESTORE_NAMESPACE" ]]; then
  echo 'AUTHENTIK_RESTORE_NAMESPACE is required' >&2
  exit 2
fi

case "$AUTHENTIK_RESTORE_NAMESPACE" in
  auth|default|kube-system|kube-public|kube-node-lease|pantry-bot)
    echo "refusing production namespace: $AUTHENTIK_RESTORE_NAMESPACE" >&2
    exit 2
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  echo 'jq is required' >&2
  exit 2
fi

run_kubectl() {
  "$KUBECTL_BIN" -n "$AUTHENTIK_RESTORE_NAMESPACE" "$@"
}

require_ready() {
  local kind="$1" name="$2"
  local status
  status="$(run_kubectl get "$kind" "$name" -o json)"
  jq -e '(.status.readyReplicas // 0) >= 1 and (.status.replicas // 0) >= 1' \
    <<<"$status" >/dev/null || {
      echo "not ready: $kind/$name" >&2
      exit 1
    }
}

require_secret_keys() {
  local name="$1" required="$2" actual key
  actual="$(run_kubectl get secret "$name" -o json | jq -r '.data // {} | keys[]' | sort)" || {
    echo "missing Secret: $name" >&2
    exit 1
  }
  for key in $required; do
    if ! grep -Fqx "$key" <<<"$actual"; then
      echo "missing key $key in Secret $name" >&2
      exit 1
    fi
  done
}

run_kubectl get namespace "$AUTHENTIK_RESTORE_NAMESPACE" -o name >/dev/null
require_ready statefulset auth-postgresql
require_ready deployment auth-authentik-server
require_ready deployment auth-authentik-worker
require_ready deployment ldap-outpost

require_secret_keys auth-authentik \
  'AUTHENTIK_BOOTSTRAP_EMAIL AUTHENTIK_BOOTSTRAP_PASSWORD AUTHENTIK_BOOTSTRAP_TOKEN AUTHENTIK_EXTERNAL_HOST AUTHENTIK_POSTGRESQL__HOST AUTHENTIK_POSTGRESQL__NAME AUTHENTIK_POSTGRESQL__PASSWORD AUTHENTIK_POSTGRESQL__PORT AUTHENTIK_POSTGRESQL__USER AUTHENTIK_SECRET_KEY'
require_secret_keys auth-postgresql 'password postgres-password'
require_secret_keys ldap-outpost-token 'AUTHENTIK_TOKEN'
require_secret_keys ldap-bind-service-account 'BIND_DN BIND_PASSWORD'
require_secret_keys ldap-chase-initial-password 'PASSWORD'

echo 'authentik_restore_contract=passed'
echo "namespace=$AUTHENTIK_RESTORE_NAMESPACE"
echo 'secret_values=not_read'
echo 'provider_behavior=follow_up_required'
echo 'session_completion=follow_up_required'
