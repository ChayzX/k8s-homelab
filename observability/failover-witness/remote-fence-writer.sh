#!/bin/sh
set -eu

# This script is installed as a forced SSH command on the home writer host.
# It deliberately accepts one operation and no arbitrary shell command. The
# authority renewer must stop before the database fence runs; otherwise a dead
# writer can retain the witness lease indefinitely.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [ "$(id -u)" -ne 0 ]; then
  echo "remote fence must run as root" >&2
  exit 77
fi

case "${SSH_ORIGINAL_COMMAND:-}" in
  "fence-pantry-postgres --confirm")
    systemctl stop pantry-postgres-self-fence.service
    if systemctl is-active --quiet pantry-postgres-self-fence.service; then
      echo "lease_renewer_stop_failed" >&2
      exit 1
    fi
    renewer_state="$(systemctl show -p ActiveState --value pantry-postgres-self-fence.service)"
    case "$renewer_state" in
      inactive|failed) ;;
      *)
        echo "lease_renewer_stop_unverified state=$renewer_state" >&2
        exit 1
        ;;
    esac
    echo "lease_renewer_stopped=verified"

    if ! env KUBECONFIG=/etc/failover-witness/pantry-postgres-fencer.kubeconfig \
      /usr/local/lib/failover-witness/fence-pantry-postgres.sh --confirm; then
      echo "scoped_database_fence_failed; applying writer-domain fallback" >&2
      /usr/local/lib/failover-witness/fence-writer-domain.sh k3s-agent.service || true
      exit 1
    fi
    ;;
  *)
    echo "unsupported remote fence operation" >&2
    exit 64
    ;;
esac
