#!/usr/bin/env sh
set -eu

[ "${1:-}" = --confirm ] && [ "$#" -eq 1 ] || {
  echo 'home PantryBot fence requires --confirm' >&2
  exit 2
}

exec /usr/bin/ssh \
  -i /home/ubuntu/.ssh/gcp-witness-oracle \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/home/ubuntu/.ssh/known_hosts \
  sa_105559435168833655240@136.113.178.106 \
  sudo /usr/local/lib/failover-witness/gcp-fence-home-writer.sh
