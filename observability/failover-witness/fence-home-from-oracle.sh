#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle-side transport for fencing the current home PantryBot database writer.
# GCP owns the private key for the loopback-only reverse SSH path to ChaseBot;
# Oracle can invoke only the root-owned wrapper on that independent witness.
GCP_HOST="136.113.178.106"
GCP_USER="sa_105559435168833655240"
SSH_KEY="/home/ubuntu/.ssh/gcp-witness-oracle"
KNOWN_HOSTS="/home/ubuntu/.ssh/known_hosts"
SSH_BIN="/usr/bin/ssh"
REMOTE_FENCE="/usr/local/lib/failover-witness/gcp-fence-home-writer.sh"

fail() {
  echo "home_writer_fence=failed reason=$1" >&2
  exit 1
}

[[ -x "$SSH_BIN" ]] || fail "ssh_unavailable"
[[ -r "$SSH_KEY" ]] || fail "ssh_key_unreadable"
[[ -r "$KNOWN_HOSTS" ]] || fail "known_hosts_unreadable"

mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || fail "explicit_confirmation_required"

if [[ "$mode" == "--dry-run" ]]; then
  "$SSH_BIN" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
    "$GCP_USER@$GCP_HOST" sudo -n test -x "$REMOTE_FENCE" \
    || fail "home_fence_transport_unavailable"
  echo "home_writer_fence=dry-run transport=gcp-reverse-ssh"
  exit 0
fi

output="$($SSH_BIN -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$KNOWN_HOSTS" -i "$SSH_KEY" \
  "$GCP_USER@$GCP_HOST" sudo -n "$REMOTE_FENCE")" \
  || fail "home_fence_command_failed"
grep -q 'lease_renewer_stopped=verified' <<<"$output" \
  || fail "home_lease_renewer_stop_unverified"
grep -q 'fence_status=passed scope=pantry-bot-postgres namespace=pantry-bot' <<<"$output" \
  || fail "home_fence_unverified"
echo "home_writer_fence=verified"
