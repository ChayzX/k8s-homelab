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
  AUTH_HOME_FENCE_COMMAND=/usr/local/sbin/fence-auth-home \
  ORACLE_KUBECTL=kubectl \
  PANTRY_PUBLIC_URLS='https://commands.example.test/ https://oauth.example.test/' \
  "$SCRIPT" --dry-run)

grep -Fq 'authority_acquired' <<<"$output"
grep -Fq 'source_fenced' <<<"$output"
grep -Fq 'auth_source_fenced' <<<"$output"
grep -Fq 'database_promoted' <<<"$output"
grep -Fq 'auth_database_promoted' <<<"$output"
grep -Fq 'database_ready' <<<"$output"
grep -Fq 'application_ready' <<<"$output"
grep -Fq 'traffic_routed' <<<"$output"

grep -Fq 'read -r -a home_fence_command' "$SCRIPT"
grep -Fq '"${home_fence_command[@]}"' "$SCRIPT"
grep -Fq 'read -r -a auth_home_fence_command' "$SCRIPT"
grep -Fq '"${auth_home_fence_command[@]}"' "$SCRIPT"
grep -Fq 'auth-postgresql-standby-0' "$SCRIPT"

echo 'pantrybot-promote-oracle-test=passed'
