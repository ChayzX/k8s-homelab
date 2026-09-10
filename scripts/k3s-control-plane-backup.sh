#!/usr/bin/env bash
set -Eeuo pipefail

# Create and verify an encrypted k3s SQLite datastore + server-token backup.
# The passphrase is entered through the caller's controlling terminal and is
# never written to the repository, command line, or backup artifact.

if [[ ${EUID} -ne 0 ]]; then
  echo 'run as root: sudo scripts/k3s-control-plane-backup.sh' >&2
  exit 1
fi

DB=/var/lib/rancher/k3s/server/db/state.db
TOKEN=/var/lib/rancher/k3s/server/token
BACKUP_ROOT=${K3S_BACKUP_ROOT:-/mnt/nvme/recovery/k3s-control-plane}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARTIFACT="$BACKUP_ROOT/k3s-${STAMP}.tar.gz.gpg"

command -v gpg >/dev/null || { echo 'gpg is required' >&2; exit 1; }
command -v sqlite3 >/dev/null || { echo 'sqlite3 is required' >&2; exit 1; }
[[ -s "$DB" ]] || { echo "missing or empty datastore: $DB" >&2; exit 1; }
[[ -s "$TOKEN" ]] || { echo "missing or empty server token: $TOKEN" >&2; exit 1; }

[[ $(sqlite3 "$DB" 'pragma quick_check;') == ok ]] || {
  echo 'SQLite quick_check failed' >&2
  exit 1
}

install -d -m 700 "$BACKUP_ROOT"
tmp=$(mktemp -d "$BACKUP_ROOT/.staging.XXXXXX")
passfile=$(mktemp "$BACKUP_ROOT/.passphrase.XXXXXX")
cleanup() {
  rm -f "$passfile"
  rm -rf "$tmp"
}
trap cleanup EXIT
chmod 600 "$passfile"

if [[ ! -r /dev/tty ]]; then
  echo 'a controlling terminal is required for passphrase entry' >&2
  exit 1
fi
read -r -s -p 'Backup passphrase: ' passphrase </dev/tty
printf '\n' >/dev/tty
read -r -s -p 'Repeat backup passphrase: ' confirmation </dev/tty
printf '\n' >/dev/tty
[[ -n "$passphrase" && "$passphrase" == "$confirmation" ]] || {
  echo 'passphrases did not match or were empty' >&2
  exit 1
}
printf '%s' "$passphrase" >"$passfile"
unset passphrase confirmation

# SQLite's online backup API gives a consistent snapshot without stopping k3s.
sqlite3 "$DB" ".backup '$tmp/state.db'"
cp --preserve=mode,timestamps "$TOKEN" "$tmp/server.token"
chmod 600 "$tmp/state.db" "$tmp/server.token"

tar -C "$tmp" -czf - state.db server.token \
  | gpg --batch --yes --pinentry-mode loopback \
      --passphrase-file "$passfile" --symmetric --cipher-algo AES256 \
      --output "$ARTIFACT"
chmod 600 "$ARTIFACT"

# Verify the encrypted artifact and its exact members without writing a
# decrypted archive to disk.
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$passfile" \
  --decrypt "$ARTIFACT" | tar -tzf - | sort | diff -u - <(printf 'server.token\nstate.db\n')

size=$(stat -c '%s' "$ARTIFACT")
sha=$(sha256sum "$ARTIFACT" | awk '{print $1}')
printf 'encrypted_backup=%s\nbytes=%s\nsha256=%s\n' "$ARTIFACT" "$size" "$sha"
