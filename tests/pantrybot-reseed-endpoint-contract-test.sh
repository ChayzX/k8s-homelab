#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/docs/recovery/PANTRYBOT-HOME-PROMOTION-STATE.md"
RETURN_MANIFEST="$ROOT/docs/recovery/pantrybot-postgres-home-return.yaml"
RESEED_MANIFEST="$ROOT/docs/recovery/pantrybot-postgres-standby-reseed-candidate.yaml"

test -f "$STATE"
test -f "$RETURN_MANIFEST"
test -f "$RESEED_MANIFEST"

# The production state record must name the exact live reseed path and warn
# that the future failback manifest has a deliberately different selector.
grep -Fq '`100.84.89.87:30432`' "$STATE"
grep -Fq '`pantry-bot/postgres-authority-replication` NodePort' "$STATE"
grep -Fq 'pantrybot.postgres/role=primary' "$STATE"
grep -Fq 'whose selector intentionally remains' "$STATE"
grep -Fq '`role=standby`' "$STATE"
grep -Fq 'Do **not** apply that manifest over the live Service' "$STATE"

# Keep the two selector contracts explicit so a later manifest edit cannot
# erase the distinction without updating the state record and this test.
grep -A4 '^  selector:$' "$RETURN_MANIFEST" | grep -Fq 'pantrybot.postgres/role: standby'
grep -A4 '^  selector:$' "$RESEED_MANIFEST" | grep -Fq 'app.kubernetes.io/name: pantry-postgres-authority-standby-reseed-v2'
grep -Fq 'name: postgres-authority-standby-reseed-v2' "$RESEED_MANIFEST"

echo 'pantrybot-reseed-endpoint-contract-test=passed'
