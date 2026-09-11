#!/bin/sh
set -eu

# This script is installed as a forced SSH command on the home writer host.
# It deliberately accepts one operation and no arbitrary shell command.
if [ "$(id -u)" -ne 0 ]; then
  echo "remote fence must run as root" >&2
  exit 77
fi

case "${SSH_ORIGINAL_COMMAND:-}" in
  fence-pantry-postgres)
    exec /usr/local/lib/failover-witness/fence-writer-domain.sh k3s-agent.service
    ;;
  *)
    echo "unsupported remote fence operation" >&2
    exit 64
    ;;
esac
