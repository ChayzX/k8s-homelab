#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin

# Daily Authentik/Postgres backup. Credentials stay inside the database and R2
# pods; this script only handles the dump stream and object paths.
NAMESPACE=auth
DB_CONTAINER=postgres
R2_NAMESPACE=jmusicbot
R2_SELECTOR='app.kubernetes.io/name=jmusicbot'
R2_CONTAINER=r2-sync
BACKUP_ROOT=/mnt/nvme/recovery/postgresql
R2_PREFIX='r2:pantry-bot-backups/recovery/auth-postgresql'
RETENTION_DAYS=30
KUBECTL_DISCOVERY_TIMEOUT=30s
KUBECTL_READY_TIMEOUT=45s
UPLOAD_TIMEOUT=300s

# The active primary StatefulSet changes during controlled failover/failback.
# Discover the Ready primary by its role label instead of pinning a historical
# pod name; fail closed if the authority is absent or ambiguous.
DB_POD="$(timeout --kill-after=5s "$KUBECTL_DISCOVERY_TIMEOUT" kubectl get pod \
  -n "$NAMESPACE" -l 'authentik.postgres/role=primary' \
  --field-selector=status.phase=Running \
  -o jsonpath='{range .items[?(@.status.containerStatuses[0].ready==true)]}{.metadata.name}{"\n"}{end}' \
  | sed '/^$/d' | head -n 2)"
if [[ -z "$DB_POD" ]]; then
  echo "backup failed: no Ready Authentik PostgreSQL primary found" >&2
  exit 1
fi
if [[ "$(printf '%s\n' "$DB_POD" | wc -l)" -ne 1 ]]; then
  echo "backup failed: multiple Ready Authentik PostgreSQL primaries found: $DB_POD" >&2
  exit 1
fi

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
# The backup is invoked with sudo from cron. If an earlier unprivileged run
# left the lock behind, protected_regular can prevent root from reopening it;
# only remove that stale file when no process currently holds it.
lock_path=/run/lock/homelab-authentik-backup.lock
if [[ -e "$lock_path" && ! -w "$lock_path" ]]; then
  if fuser "$lock_path" >/dev/null 2>&1; then
    echo 'backup already running'
    exit 0
  fi
  rm -f "$lock_path"
fi
exec 9>"$lock_path"
flock -n 9 || { echo 'backup already running'; exit 0; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="authentik-${stamp}.dump.gz"
tmp="$(mktemp "$BACKUP_ROOT/.${name}.XXXXXX")"
local_path="$BACKUP_ROOT/$name"
trap 'rm -f "$tmp"' EXIT

echo "creating $name"
kubectl exec -n "$NAMESPACE" "$DB_POD" -c "$DB_CONTAINER" -- sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  | gzip -c > "$tmp"
gzip -t "$tmp"
size="$(stat -c '%s' "$tmp")"
test "$size" -gt 1000000
chmod 600 "$tmp"
mv "$tmp" "$local_path"

# Keep a verified local recovery point even when the optional R2 sidecar is
# unavailable. Return non-zero so the scheduler/monitor still records the
# remote-upload gate as failed; never claim a remote backup that did not exist.
R2_POD="$(timeout --kill-after=5s "$KUBECTL_DISCOVERY_TIMEOUT" kubectl get pod \
  -n "$R2_NAMESPACE" -l "$R2_SELECTOR" --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$R2_POD" ]]; then
  echo "backup warning: local backup verified but R2 sidecar is unavailable: $local_path" >&2
  exit 2
fi
R2_READY="$(timeout --kill-after=5s "$KUBECTL_READY_TIMEOUT" kubectl get pod \
  -n "$R2_NAMESPACE" "$R2_POD" \
  -o jsonpath='{.status.containerStatuses[?(@.name=="r2-sync")].ready}')"
if [[ "$R2_READY" != true ]]; then
  echo "backup warning: local backup verified but R2 sidecar is not Ready: $R2_POD" >&2
  exit 2
fi

echo "uploading $name to R2"
remote_tmp="/tmp/$name"
cleanup_remote() {
  timeout --kill-after=5s 30s kubectl exec -n "$R2_NAMESPACE" "$R2_POD" \
    -c "$R2_CONTAINER" -- rm -f -- "$remote_tmp" >/dev/null 2>&1 || true
}
trap cleanup_remote EXIT

# `kubectl exec -i` is prone to hanging while streaming a large dump through
# the exec channel. kubectl cp uses the pod's tar implementation instead; the
# rclone operation is then a separate bounded, observable command.
timeout --kill-after=15s "$UPLOAD_TIMEOUT" kubectl cp "$local_path" \
  "$R2_NAMESPACE/$R2_POD:$remote_tmp" -c "$R2_CONTAINER"

timeout --kill-after=15s "$UPLOAD_TIMEOUT" kubectl exec -n "$R2_NAMESPACE" \
  "$R2_POD" -c "$R2_CONTAINER" -- sh -ceu '
    tmp_path="$1"
    remote_path="$2"
    expected_size="$3"
    tmp_dir="${tmp_path%/*}"
    remote_dir="${remote_path%/*}"
    remote_name="${remote_path##*/}"
    cleanup() { rm -f -- "$tmp_path"; }
    trap cleanup EXIT HUP INT TERM
    rclone copyto "$tmp_path" "$remote_path" --config=/dev/null \
      --s3-no-check-bucket --timeout=2m --contimeout=15s --retries=2 \
      --low-level-retries=5
    # Verify the remote object using the backend hash, not just a successful
    # HTTP upload. Fail closed if the provider cannot return a comparable hash.
    # rclone check compares directory trees; constrain both sides to this
    # exact basename rather than passing an object path as a filesystem root.
    rclone check "$tmp_dir" "$remote_dir" --one-way --include "$remote_name" \
      --s3-no-check-bucket --timeout=2m --contimeout=15s --retries=2 \
      --low-level-retries=5
    remote_size="$(rclone size "$remote_path" --json --config=/dev/null \
      --s3-no-check-bucket --timeout=2m --contimeout=15s --retries=2 \
      --low-level-retries=5 \
      | grep -o "\\\"bytes\\\":[0-9]*" | cut -d: -f2)"
    test "$remote_size" = "$expected_size"
    echo "remote_size=$remote_size"
  ' sh "$remote_tmp" "$R2_PREFIX/$name" "$size"

remote_size="$size"
find "$BACKUP_ROOT" -type f -name 'authentik-*.dump.gz' -mtime +"$RETENTION_DAYS" -delete

sha256sum "$local_path"
echo "backup complete: local_bytes=$size remote_size=$remote_size path=$R2_PREFIX/$name"
