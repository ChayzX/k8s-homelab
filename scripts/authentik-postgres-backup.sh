#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

# Daily Authentik/Postgres backup. Credentials stay inside the database and R2
# pods; this script only handles the dump stream and object paths.
NAMESPACE=auth
DB_POD=auth-postgresql-0
DB_CONTAINER=postgresql
R2_NAMESPACE=jmusicbot
R2_SELECTOR='app.kubernetes.io/name=jmusicbot'
R2_CONTAINER=r2-sync
BACKUP_ROOT=/mnt/nvme/recovery/postgresql
R2_PREFIX='r2:pantry-bot-backups/recovery/auth-postgresql'
RETENTION_DAYS=30

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
exec 9>"/tmp/homelab-authentik-backup.lock"
flock -n 9 || { echo 'backup already running'; exit 0; }

R2_POD="$(kubectl get pod -n "$R2_NAMESPACE" -l "$R2_SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
test -n "$R2_POD"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="authentik-${stamp}.dump.gz"
tmp="$(mktemp "$BACKUP_ROOT/.${name}.XXXXXX")"
local_path="$BACKUP_ROOT/$name"
trap 'rm -f "$tmp"' EXIT

echo "creating $name"
kubectl exec -n "$NAMESPACE" "$DB_POD" -c "$DB_CONTAINER" -- sh -c \
  'PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  | gzip -c > "$tmp"
gzip -t "$tmp"
size="$(stat -c '%s' "$tmp")"
test "$size" -gt 1000000
chmod 600 "$tmp"
mv "$tmp" "$local_path"

echo "uploading $name to R2"
kubectl exec -i -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- sh -c \
  "cat > /tmp/$name && rclone copyto /tmp/$name '$R2_PREFIX/$name' --config=/dev/null --s3-no-check-bucket && rm -f /tmp/$name" \
  < "$local_path"

remote_size="$(kubectl exec -n "$R2_NAMESPACE" "$R2_POD" -c "$R2_CONTAINER" -- \
  rclone size "$R2_PREFIX/$name" --config=/dev/null --s3-no-check-bucket \
  | awk '/Total size:/ {print $3; exit}')"
test -n "$remote_size"
find "$BACKUP_ROOT" -type f -name 'authentik-*.dump.gz' -mtime +"$RETENTION_DAYS" -delete

sha256sum "$local_path"
echo "backup complete: local_bytes=$size remote_size=$remote_size path=$R2_PREFIX/$name"
