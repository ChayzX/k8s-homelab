#!/usr/bin/env bash
# Contract for pantry-bot/35-postgres-cronjobs.yaml (ChayzX/pantry-bot#348):
# the CronJobs must wait (bounded) for PostgreSQL and log which step failed.
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
f="$root/pantry-bot/35-postgres-cronjobs.yaml"
test -f "$f"

test "$(grep -c '^kind:' "$f")" = 2
grep -q '^kind: CronJob$' "$f"
! grep -Eq '^kind: (StatefulSet|Service|Secret|Deployment)$' "$f"
grep -q '^  name: postgres-backup$' "$f"
grep -q '^  name: postgres-analyze$' "$f"

test "$(grep -c 'pg_isready' "$f")" -ge 2
test "$(grep -c 'while \[ "\$i" -lt 30 \]' "$f")" = 2
test "$(grep -c 'sleep 2' "$f")" = 2
test "$(grep -c 'FAILED (exit \$rc) during step' "$f")" = 2
# Backup/verify logic must be preserved.
for needle in 'pg_is_in_recovery' 'gzip -t' '-lt 200000' 'verified $dest/$base' 'rclone delete'; do
  grep -qF -- "$needle" "$f" || { echo "missing preserved logic: $needle" >&2; exit 1; }
done
# Never log the connection URL.
! grep -Eq 'echo[^#]*\$\{?PANTRY_DATABASE_URL' "$f" || { echo "connection URL must not be echoed" >&2; exit 1; }

echo "pantrybot-postgres-cronjobs-test=passed"
