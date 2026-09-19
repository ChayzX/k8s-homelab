#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-side transport for fencing the current home PantryBot database writer.
# The target, user, and command are intentionally fixed; no arbitrary remote
# command is accepted from the environment.
HOME_HOST="${PANTRY_HOME_FENCE_HOST:-100.84.89.87}"
HOME_USER="${PANTRY_HOME_FENCE_USER:-chase}"
SSH_KEY="${PANTRY_HOME_FENCE_KEY:-/root/.ssh/pantry-home-fence}"
KNOWN_HOSTS="${PANTRY_HOME_FENCE_KNOWN_HOSTS:-/root/.ssh/known_hosts}"
SSH_BIN="${PANTRY_HOME_FENCE_SSH:-/usr/bin/ssh}"

fail() {
  echo "home_writer_fence=failed reason=$1" >&2
  exit 1
}

[[ "$HOME_HOST" == "100.84.89.87" ]] || fail "invalid_home_identity"
[[ "$HOME_USER" == "chase" ]] || fail "invalid_home_user"
[[ -x "$SSH_BIN" ]] || fail "ssh_unavailable"
[[ -r "$SSH_KEY" ]] || fail "ssh_key_unreadable"
[[ -r "$KNOWN_HOSTS" ]] || fail "known_hosts_unreadable"

mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || fail "explicit_confirmation_required"

if [[ "$mode" == "--dry-run" ]]; then
  "$SSH_BIN" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
    "$HOME_USER@$HOME_HOST" sudo -n /usr/local/lib/failover-witness/fence-pantry-postgres.sh --help >/dev/null \
    || fail "home_fence_transport_unavailable"
  echo "home_writer_fence=dry-run host=$HOME_HOST"
  exit 0
fi

output="$($SSH_BIN -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
  "$HOME_USER@$HOME_HOST" sudo -n /usr/local/lib/failover-witness/fence-pantry-postgres.sh --confirm)" \
  || fail "home_fence_command_failed"
grep -q 'fence_status=passed scope=pantry-bot-postgres namespace=pantry-bot' <<<"$output" \
  || fail "home_fence_unverified"
echo "home_writer_fence=verified"
