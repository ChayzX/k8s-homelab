#!/usr/bin/env bash
set -Eeuo pipefail

# k3s units use KillMode=process, and containerd places pod processes in
# separate kubepods cgroups. Stopping k3s can therefore leave a PostgreSQL
# process alive outside the systemd unit cgroup.
unit=${1:?usage: fence-writer-domain.sh <k3s.service|k3s-agent.service>}
case "$unit" in
  k3s.service|k3s-agent.service) ;;
  *) echo "refusing unexpected unit: $unit" >&2; exit 2 ;;
esac

systemctl stop "$unit" || true
# systemd can report the unit inactive while its KillMode=process children
# remain in the cgroup, so this must run unconditionally after stop.
systemctl kill --kill-who=all --signal=SIGKILL "$unit" || true
if systemctl is-active --quiet "$unit"; then
  echo "writer domain remains active: $unit" >&2
  exit 1
fi
cgroup=$(systemctl show -p ControlGroup --value "$unit")
if [ -n "$cgroup" ] && [ -s "/sys/fs/cgroup${cgroup}/cgroup.procs" ]; then
  echo "writer-domain cgroup still has processes: $unit" >&2
  cat "/sys/fs/cgroup${cgroup}/cgroup.procs" >&2
  exit 1
fi

# Fallback for k3s pod cgroups: find every PostgreSQL process left by the
# stopped writer domain and kill the complete cgroup containing it. This is
# intentionally scoped to cgroups discovered through a postgres process; it
# does not kill unrelated pods or host services.
declare -A postgres_cgroups=()
for pid in $(pgrep -x postgres || true); do
  path=$(awk -F: '$1 == "0" { print $3 }' "/proc/$pid/cgroup" 2>/dev/null || true)
  if [ -n "$path" ] && [ -f "/sys/fs/cgroup${path}/cgroup.procs" ]; then
    postgres_cgroups["$path"]=1
  fi
done

for path in "${!postgres_cgroups[@]}"; do
  procs="/sys/fs/cgroup${path}/cgroup.procs"
  mapfile -t pids < "$procs" || true
  if [ "${#pids[@]}" -gt 0 ]; then
    kill -KILL "${pids[@]}" 2>/dev/null || true
  fi
done

# Give containerd a moment to reap the shim, then reject fencing if any
# PostgreSQL process survived or was immediately recreated.
sleep 0.2
if pgrep -x postgres >/dev/null; then
  echo "PostgreSQL writer process survived fencing: $unit" >&2
  pgrep -a -x postgres >&2 || true
  exit 1
fi
printf 'writer_domain_fenced=%s\n' "$unit"
