#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

# Upload the newest local Minecraft archive to R2. The local backup generator
# remains responsible for producing a consistent archive; this script only
# copies an already-created tarball and never reads the live world directly.
BACKUP_ROOT=/home/chase/minecraft-backups
R2_NAMESPACE=jmusicbot
R2_SELECTOR='app.kubernetes.io/name=jmusicbot'
R2_CONTAINER=r2-sync
R2_PREFIX='r2:pantry-bot-backups/recovery/minecraft'

exec 9>"/tmp/homelab-minecraft-offsite-backup.lock"
flock -n 9 || { echo 'backup upload already running'; exit 0; }

latest="$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'world-backup-*.tar.gz' -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p')"
test -n "$latest"
gzip -t "$latest"
name="$(basename "$latest")"
R2_POD="$(kubectl get pod -n "$R2_NAMESPACE" -l "$R2_SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
test -n "$R2_POD"

echo "uploading $latest"
kubectl exec -i -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- sh -c \
  "cat > /tmp/$name && rclone copyto /tmp/$name '$R2_PREFIX/$name' --config=/dev/null --s3-no-check-bucket && rm -f /tmp/$name" \
  < "$latest"
remote_size="$(kubectl exec -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- \
  rclone size "$R2_PREFIX/$name" --config=/dev/null --s3-no-check-bucket \
  | awk '/Total size:/ {print $3; exit}')"
test -n "$remote_size"
sha256sum "$latest"
echo "offsite backup complete: remote_size=$remote_size path=$R2_PREFIX/$name"
