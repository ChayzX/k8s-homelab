#!/usr/bin/env bash
set -Eeuo pipefail

# Non-destructive rehearsal for an encrypted k3s datastore/token artifact.
# This validates decryption, archive contents, SQLite integrity, and the
# expected Kine schema without replacing or starting the live control plane.

if [[ ${EUID} -ne 0 ]]; then
  echo 'run as root: sudo scripts/k3s-control-plane-restore-check.sh [artifact]' >&2
  exit 1
fi

artifact=${1:-}
if [[ -z "$artifact" ]]; then
  artifact=$(find /mnt/nvme/recovery/k3s-control-plane -maxdepth 1 -type f \
    -name 'k3s-*.tar.gz.gpg' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
fi
[[ -n "$artifact" && -f "$artifact" ]] || {
  echo 'encrypted k3s artifact not found' >&2
  exit 1
}
command -v gpg >/dev/null || { echo 'gpg is required' >&2; exit 1; }
command -v sqlite3 >/dev/null || { echo 'sqlite3 is required' >&2; exit 1; }
[[ -r /dev/tty ]] || { echo 'a controlling terminal is required' >&2; exit 1; }

restore_dir=$(mktemp -d /mnt/nvme/recovery/k3s-restore-check.XXXXXX)
passfile=$(mktemp /mnt/nvme/recovery/.restore-passphrase.XXXXXX)
chmod 700 "$restore_dir"
chmod 600 "$passfile"
cleanup() {
  rm -f "$passfile"
  rm -rf "$restore_dir"
}
trap cleanup EXIT

read -r -s -p 'Backup passphrase: ' passphrase </dev/tty
printf '\n' >/dev/tty
[[ -n "$passphrase" ]] || { echo 'passphrase cannot be empty' >&2; exit 1; }
printf '%s' "$passphrase" >"$passfile"
unset passphrase

gpg --batch --yes --pinentry-mode loopback --passphrase-file "$passfile" \
  --decrypt "$artifact" \
  | tar -xzf - -C "$restore_dir"

[[ -s "$restore_dir/state.db" ]] || { echo 'restored state.db is empty' >&2; exit 1; }
[[ -s "$restore_dir/server.token" ]] || { echo 'restored server.token is empty' >&2; exit 1; }
[[ $(sqlite3 "$restore_dir/state.db" 'pragma quick_check;') == ok ]] || {
  echo 'restored SQLite quick_check failed' >&2
  exit 1
}
sqlite3 "$restore_dir/state.db" \
  "select case when count(*) > 0 then 'kine_rows=present' else 'kine_rows=missing' end from kine;" \
  | grep -qx 'kine_rows=present'

printf 'restore_check=passed\nartifact=%s\n' "$artifact"
