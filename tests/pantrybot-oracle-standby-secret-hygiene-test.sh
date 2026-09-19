#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for manifest in \
  "$ROOT/docs/recovery/pantrybot-postgres-standby-oracle.yaml" \
  "$ROOT/docs/recovery/pantrybot-postgres-authority-standby-oracle.yaml"
do
  grep -q 'data/.pgpass' "$manifest"
  grep -q 'chmod 600 /var/lib/postgresql/data/.pgpass' "$manifest"
  grep -q "sed -i 's#/tmp/pgpass#/var/lib/postgresql/data/.pgpass#g'" "$manifest"
  if grep -Eq 'primary_conninfo.*password=' "$manifest"; then
    echo "plaintext primary_conninfo password found in $manifest" >&2
    exit 1
  fi
done

echo 'pantrybot-oracle-standby-secret-hygiene-test=passed'
