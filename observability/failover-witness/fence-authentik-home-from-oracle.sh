#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-side transport for fencing the Home Authentik PostgreSQL writer.
# The target and remote operation are fixed; this adapter never accepts an
# arbitrary SSH command from configuration.
HOME_HOST="${AUTHENTIK_HOME_FENCE_HOST:-100.84.89.87}"
HOME_USER="${AUTHENTIK_HOME_FENCE_USER:-chase}"
SSH_KEY="${AUTHENTIK_HOME_FENCE_KEY:-/root/.ssh/authentik-home-fence}"
KNOWN_HOSTS="${AUTHENTIK_HOME_FENCE_KNOWN_HOSTS:-/root/.ssh/known_hosts}"
SSH_BIN="${AUTHENTIK_HOME_FENCE_SSH:-/usr/bin/ssh}"

fail() { echo "authentik_home_fence=failed reason=$1" >&2; exit 1; }

[[ "$HOME_HOST" == "100.84.89.87" ]] || fail "invalid_home_identity"
[[ "$HOME_USER" == "chase" ]] || fail "invalid_home_user"
[[ -x "$SSH_BIN" ]] || fail "ssh_unavailable"
[[ -r "$SSH_KEY" ]] || fail "ssh_key_unreadable"
[[ -r "$KNOWN_HOSTS" ]] || fail "known_hosts_unreadable"

mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || fail "explicit_confirmation_required"

remote_fence() {
  "$SSH_BIN" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
    "$HOME_USER@$HOME_HOST" sudo -n /usr/local/lib/failover-witness/fence-authentik-postgres-home.sh "$1"
}

if [[ "$mode" == "--dry-run" ]]; then
  output="$(remote_fence --dry-run)" || fail "home_fence_transport_unavailable"
  grep -q 'fence_status=dry-run scope=home-authentik-postgres' <<<"$output" || fail "home_fence_unverified"
  echo "authentik_home_fence=dry-run host=$HOME_HOST"
  exit 0
fi

output="$(remote_fence --confirm)" || fail "home_fence_command_failed"
grep -q 'fence_status=passed scope=home-authentik-postgres' <<<"$output" || fail "home_fence_unverified"
echo "authentik_home_fence=verified"
