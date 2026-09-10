#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

# Back up the non-secret Minecraft runtime configuration separately from the
# large world archive. Secret-bearing properties and the Floodgate key are
# deliberately excluded; those must be reconstructed from the secret store.
BACKUP_ROOT=/home/chase/minecraft-backups
R2_NAMESPACE=jmusicbot
R2_SELECTOR='app.kubernetes.io/name=jmusicbot'
R2_CONTAINER=r2-sync
R2_PREFIX='r2:pantry-bot-backups/recovery/minecraft-config'

exec 9>/tmp/homelab-minecraft-config-offsite-backup.lock
flock -n 9 || { echo 'config backup upload already running'; exit 0; }

timestamp="$(date -u +%Y%m%d-%H%M%S)"
name="minecraft-config-${timestamp}.tar.gz"
archive="$BACKUP_ROOT/$name"
mkdir -p "$BACKUP_ROOT"

# Build the archive inside the running pod so the host never needs access to
# the root-owned local-path PVC. server.properties is sanitized before it is
# archived; plugin jars and generated caches are supplied by the image.
kubectl exec -n minecraft deploy/minecraft -- sh -c '
  set -eu
  stage="$(mktemp -d)"
  trap "rm -rf \"$stage\"" EXIT
  for file in eula.txt server.properties bukkit.yml spigot.yml commands.yml permissions.yml banned-ips.json banned-players.json ops.json whitelist.json; do
    [ -f "/data/$file" ] && mkdir -p "$stage/$(dirname "$file")" && cp "/data/$file" "$stage/$file"
  done
  for file in config/paper-global.yml config/paper-world-defaults.yml plugins/Geyser-Spigot/config.yml plugins/floodgate/config.yml plugins/spark/config.json; do
    [ -f "/data/$file" ] && mkdir -p "$stage/$(dirname "$file")" && cp "/data/$file" "$stage/$file"
  done
  sed -Ei "/^(rcon\.password|management-server-secret|management-server-tls-keystore|management-server-tls-keystore-password)=/d; s/^enable-rcon=.*/enable-rcon=false/" "$stage/server.properties"
  tar -czf - -C "$stage" .
' > "$archive"

gzip -t "$archive"
R2_POD="$(kubectl get pod -n "$R2_NAMESPACE" -l "$R2_SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
test -n "$R2_POD"
kubectl exec -i -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- sh -c \
  "cat > /tmp/$name && rclone copyto /tmp/$name '$R2_PREFIX/$name' --config=/dev/null --s3-no-check-bucket && rm -f /tmp/$name" \
  < "$archive"
remote_size="$(kubectl exec -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- \
  rclone size "$R2_PREFIX/$name" --config=/dev/null --s3-no-check-bucket \
  | awk '/Total size:/ {print $3; exit}')"
test -n "$remote_size"
chmod 600 "$archive"
echo "config backup complete: local=$archive remote_size=$remote_size path=$R2_PREFIX/$name"

# Keep a month of small configuration generations locally.
find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'minecraft-config-*.tar.gz' -mtime +30 -delete
