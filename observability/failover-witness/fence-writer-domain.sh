#!/usr/bin/env bash
set -Eeuo pipefail

# k3s units use KillMode=process, so stopping k3s can leave containerd shims
# and a PostgreSQL process alive. Fence the complete unit cgroup.
unit=${1:?usage: fence-writer-domain.sh <k3s.service|k3s-agent.service>}
case "$unit" in
  k3s.service|k3s-agent.service) ;;
  *) echo "refusing unexpected unit: $unit" >&2; exit 2 ;;
esac

systemctl stop "$unit" || true
if systemctl is-active --quiet "$unit"; then
  systemctl kill --kill-who=all --signal=SIGKILL "$unit"
fi
if systemctl is-active --quiet "$unit"; then
  echo "writer domain remains active: $unit" >&2
  exit 1
fi
printf 'writer_domain_fenced=%s\n' "$unit"
