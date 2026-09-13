#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT/scripts/pantrybot-promote-oracle.sh"

if "$SCRIPT" --confirm >/dev/null 2>&1; then
  echo 'promotion must refuse missing configuration' >&2
  exit 1
fi

output=$(PANTRY_WITNESS_URL=http://witness.invalid \
  PANTRY_WITNESS_SECRET_FILE=/run/secret \
  HOME_FENCE_COMMAND=/usr/local/sbin/fence-home \
  ORACLE_KUBECTL=kubectl \
  PANTRY_PUBLIC_URLS='https://commands.example.test/ https://oauth.example.test/' \
  "$SCRIPT" --dry-run)

grep -Fq 'authority_acquired' <<<"$output"
grep -Fq 'source_fenced' <<<"$output"
grep -Fq 'database_promoted' <<<"$output"
grep -Fq 'database_ready' <<<"$output"
grep -Fq 'application_ready' <<<"$output"
grep -Fq 'traffic_routed' <<<"$output"

echo 'pantrybot-promote-oracle-test=passed'
