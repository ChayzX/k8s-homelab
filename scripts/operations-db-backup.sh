#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

# Consistent SQLite backup for operations-web. The app's Python sqlite3 backup
# API snapshots the live database; copying the WAL file directly is unsafe.
NAMESPACE=operations
APP_POD_SELECTOR='app.kubernetes.io/name=operations-web'
R2_NAMESPACE=jmusicbot
R2_SELECTOR='app.kubernetes.io/name=jmusicbot'
R2_CONTAINER=r2-sync
BACKUP_ROOT=/mnt/nvme/recovery/operations
R2_PREFIX='r2:pantry-bot-backups/recovery/operations'
RETENTION_DAYS=30

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
exec 9>"/tmp/homelab-operations-backup.lock"
flock -n 9 || { echo 'backup already running'; exit 0; }

APP_POD="$(kubectl get pod -n "$NAMESPACE" -l "$APP_POD_SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
R2_POD="$(kubectl get pod -n "$R2_NAMESPACE" -l "$R2_SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
test -n "$APP_POD"; test -n "$R2_POD"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="operations-${stamp}.db.gz"
tmp="$(mktemp "$BACKUP_ROOT/.${name}.XXXXXX")"
local_path="$BACKUP_ROOT/$name"
trap 'rm -f "$tmp"' EXIT

kubectl exec -n "$NAMESPACE" "$APP_POD" -- python3 -c \
  'import sqlite3; src=sqlite3.connect("/data/operations.db"); dst=sqlite3.connect("/tmp/operations-recovery.db"); src.backup(dst); dst.close(); src.close()'
kubectl exec -n "$NAMESPACE" "$APP_POD" -- cat /tmp/operations-recovery.db \
  | gzip -c > "$tmp"
kubectl exec -n "$NAMESPACE" "$APP_POD" -- rm -f /tmp/operations-recovery.db
gzip -t "$tmp"
size="$(stat -c '%s' "$tmp")"
test "$size" -gt 1000
chmod 600 "$tmp"
mv "$tmp" "$local_path"

kubectl exec -i -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- sh -c \
  "cat > /tmp/$name && rclone copyto /tmp/$name '$R2_PREFIX/$name' --config=/dev/null --s3-no-check-bucket && rm -f /tmp/$name" \
  < "$local_path"
remote_size="$(kubectl exec -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- \
  rclone size "$R2_PREFIX/$name" --config=/dev/null --s3-no-check-bucket \
  | awk '/Total size:/ {print $3; exit}')"
test -n "$remote_size"
find "$BACKUP_ROOT" -type f -name 'operations-*.db.gz' -mtime +"$RETENTION_DAYS" -delete

sha256sum "$local_path"
echo "backup complete: local_bytes=$size remote_size=$remote_size path=$R2_PREFIX/$name"
