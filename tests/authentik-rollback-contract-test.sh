#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CHECKER="$ROOT/scripts/authentik-rollback-contract-check.sh"
PROMOTION="$ROOT/scripts/pantrybot-promote-oracle.sh"
RUNBOOK="$ROOT/docs/recovery/runbooks/authentik.md"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if "$CHECKER" --promotion "$PROMOTION" --runbook "$RUNBOOK" >"$TMP/pass.out" 2>&1; then
  grep -qx 'authentik_rollback_contract=passed' "$TMP/pass.out"
else
  echo 'complete Authentik rollback contract was rejected' >&2
  cat "$TMP/pass.out" >&2
  exit 1
fi

cp "$RUNBOOK" "$TMP/unsafe-runbook.md"
sed -i 's/Fence the promoted writer, re-seed home if needed, promote home/Fence the promoted writer, promote home/' \
  "$TMP/unsafe-runbook.md"
if "$CHECKER" --promotion "$PROMOTION" --runbook "$TMP/unsafe-runbook.md" >"$TMP/fail.out" 2>&1; then
  echo 'rollback contract accepted a runbook without re-seeding guidance' >&2
  exit 1
fi
grep -q 'missing rollback requirement: re-seed home' "$TMP/fail.out"

echo 'authentik-rollback-contract-test=passed'
