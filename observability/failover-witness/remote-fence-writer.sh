#!/bin/sh
set -eu

# Forced SSH command on ChaseBot. It accepts exactly one operation and uses a
# root-owned, least-privilege kubeconfig to fence only home PantryBot
# PostgreSQL; it never stops k3s or Minecraft.
if [ "$(id -u)" -ne 0 ]; then
  echo "remote fence must run as root" >&2
  exit 77
fi

case "${SSH_ORIGINAL_COMMAND:-}" in
  "fence-pantry-postgres --confirm")
    exec env KUBECONFIG=/etc/failover-witness/pantry-postgres-fencer.kubeconfig \
      /usr/local/lib/failover-witness/fence-pantry-postgres.sh --confirm
    ;;
  "fence-auth-postgres --confirm")
    exec env KUBECONFIG=/etc/failover-witness/auth-postgres-fencer.kubeconfig \
      /usr/local/lib/failover-witness/fence-auth-postgres.sh --confirm
    ;;
  *)
    echo "unsupported remote fence operation" >&2
    exit 64
    ;;
esac
