#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT/docs/recovery/pantrybot-postgres-home-return.yaml"

grep -q '^  replicas: 0$' "$MANIFEST"
grep -q 'test -f /var/lib/postgresql/data/standby.signal' "$MANIFEST"
grep -q 'existing PVC is not a PostgreSQL standby' "$MANIFEST"
grep -q "select pg_is_in_recovery()" "$MANIFEST"
grep -q 'nodePort: 30432' "$MANIFEST"

echo 'pantrybot-home-return-standby-test=passed'
