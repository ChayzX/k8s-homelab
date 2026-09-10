#!/usr/bin/env bash
set -Eeuo pipefail

# Start a restored k3s SQLite/Kine control plane in a disposable, isolated
# Docker network. This never mounts the live k3s directory and never changes
# the live service. The caller must provide the backup passphrase through the
# controlling terminal.

usage() {
  cat <<'EOF'
Usage: sudo scripts/k3s-control-plane-isolated-restore.sh [options]

Non-production isolated restore rehearsal. The live k3s service and datastore
are not modified. The temporary API is published only on 127.0.0.1.

Options:
  --artifact PATH       encrypted k3s backup (default: newest local artifact)
  --image IMAGE         matching rancher/k3s image (default: rancher/k3s:v1.36.3-k3s1)
  --port PORT           localhost API port (default: 16443)
  --timeout SECONDS     startup timeout (default: 120)
  --help                show this help
EOF
}

artifact=''
image='rancher/k3s:v1.36.3-k3s1'
port=16443
timeout_seconds=120

while (($#)); do
  case "$1" in
    --artifact) artifact=${2:?missing path for --artifact}; shift 2 ;;
    --image) image=${2:?missing image for --image}; shift 2 ;;
    --port) port=${2:?missing port for --port}; shift 2 ;;
    --timeout) timeout_seconds=${2:?missing seconds for --timeout}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo 'run as root: sudo scripts/k3s-control-plane-isolated-restore.sh' >&2
  exit 1
fi

command -v docker >/dev/null || { echo 'docker is required' >&2; exit 1; }
command -v gpg >/dev/null || { echo 'gpg is required' >&2; exit 1; }
command -v tar >/dev/null || { echo 'tar is required' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl is required' >&2; exit 1; }
[[ "$port" =~ ^[0-9]+$ && "$port" -ge 1024 && "$port" -le 65535 ]] || {
  echo 'port must be an integer from 1024 through 65535' >&2
  exit 2
}
[[ "$timeout_seconds" =~ ^[0-9]+$ && "$timeout_seconds" -gt 0 ]] || {
  echo 'timeout must be a positive integer' >&2
  exit 2
}

if [[ -z "$artifact" ]]; then
  artifact=$(find /mnt/nvme/recovery/k3s-control-plane -maxdepth 1 -type f \
    -name 'k3s-*.tar.gz.gpg' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
fi
[[ -n "$artifact" && -f "$artifact" ]] || {
  echo 'encrypted k3s artifact not found' >&2
  exit 1
}

work_dir=$(mktemp -d /mnt/nvme/recovery/k3s-isolated-restore.XXXXXX)
passfile=$(mktemp /mnt/nvme/recovery/.k3s-isolated-passphrase.XXXXXX)
container="k3s-isolated-restore-$$"
network="k3s-isolated-net-$$"
cleanup() {
  status=$?
  if docker container inspect "$container" >/dev/null 2>&1; then
    if ((status != 0)); then
      docker logs "$container" 2>&1 | tail -80 >&2 || true
    fi
    docker rm -f "$container" >/dev/null 2>&1 || true
  fi
  docker network inspect "$network" >/dev/null 2>&1 && docker network rm "$network" >/dev/null 2>&1 || true
  rm -f "$passfile"
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT
chmod 700 "$work_dir"
chmod 600 "$passfile"

[[ -r /dev/tty ]] || { echo 'a controlling terminal is required' >&2; exit 1; }
read -r -s -p 'Backup passphrase: ' passphrase </dev/tty
printf '\n' >/dev/tty
[[ -n "$passphrase" ]] || { echo 'passphrase cannot be empty' >&2; exit 1; }
printf '%s' "$passphrase" >"$passfile"
unset passphrase

mkdir -p "$work_dir/extracted" "$work_dir/data/server/db"
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$passfile" \
  --decrypt "$artifact" | tar -xzf - -C "$work_dir/extracted"
[[ -s "$work_dir/extracted/state.db" && -s "$work_dir/extracted/server.token" ]] || {
  echo 'artifact is missing state.db or server.token' >&2
  exit 1
}
cp --preserve=mode,timestamps "$work_dir/extracted/state.db" "$work_dir/data/server/db/state.db"
cp --preserve=mode,timestamps "$work_dir/extracted/server.token" "$work_dir/data/server/token"
chmod 600 "$work_dir/data/server/db/state.db" "$work_dir/data/server/token"

# K3s requires a default route during startup. A user-defined bridge still
# isolates the container from the host's live k3s filesystem and services; do
# not use Docker's `--internal` mode here because it removes the default route.
docker network create "$network" >/dev/null
docker run --detach --name "$container" --privileged \
  --network "$network" --publish "127.0.0.1:${port}:6443" \
  --volume "$work_dir/data:/var/lib/rancher/k3s" \
  "$image" server \
    --data-dir /var/lib/rancher/k3s \
    --https-listen-port 6443 \
    --advertise-address 127.0.0.1 \
    --tls-san 127.0.0.1 \
    --egress-selector-mode disabled \
    --disable-agent \
    --disable=traefik \
    --disable=servicelb \
    --disable=local-storage \
    --write-kubeconfig-mode 600 >/dev/null

deadline=$((SECONDS + timeout_seconds))
api_ready=0
while ((SECONDS < deadline)); do
  if docker exec "$container" kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml \
      get --raw=/version >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  sleep 2
done
((api_ready == 1)) || { echo 'isolated k3s API did not become healthy' >&2; exit 1; }

kubectl_output=$(docker exec "$container" kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml \
  get namespaces -o name)
grep -q '^namespace/jmusicbot$' <<<"$kubectl_output" || {
  echo 'restored API is healthy but expected namespace data is missing' >&2
  exit 1
}
grep -q '^namespace/observability$' <<<"$kubectl_output" || {
  echo 'restored API is healthy but expected observability namespace is missing' >&2
  exit 1
}

printf 'isolated_restore=passed\nartifact=%s\nimage=%s\napi=https://127.0.0.1:%s\n' \
  "$artifact" "$image" "$port"
