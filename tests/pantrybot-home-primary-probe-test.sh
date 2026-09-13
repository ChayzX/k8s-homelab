#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
script="$root/scripts/probe-home-primary-postgres.sh"

test -x "$script"
bash -n "$script"
grep -q 'pg_is_in_recovery' "$script"
grep -q 'transaction_read_only' "$script"
grep -q 'CREATE TEMP TABLE' "$script"
grep -q 'ON_ERROR_STOP' "$script"
grep -q 'PGPASSFILE' "$script"

echo 'pantrybot-home-primary-probe-test=passed'
