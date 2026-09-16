#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT/scripts/pantrybot-promote-site.sh"

if "$SCRIPT" --confirm >/dev/null 2>&1; then
  echo 'promotion must refuse missing configuration' >&2
  exit 1
fi

output=$(PROMOTION_SITE=oracle \
  WITNESS_URL=http://witness.invalid \
  WITNESS_SHARED_SECRET=test \
  OLD_WRITER_FENCE_COMMAND='/usr/local/sbin/fence-home --confirm' \
  PANTRY_PUBLIC_URLS='https://commands.example.test/ https://oauth.example.test/' \
  "$SCRIPT" --dry-run)

grep -Fq 'promotion_site=oracle' <<<"$output"
grep -Fq 'old_writer_fence_validated' <<<"$output"
grep -Fq 'witness_validated' <<<"$output"
grep -Fq 'oracle_database_promoted' <<<"$output"
grep -Fq 'application_ready' <<<"$output"

# The site-neutral adapter must not couple Authentik into PantryBot promotion.
if grep -Eq 'auth_|authentik_|auth-postgresql' "$SCRIPT"; then
  echo 'promotion must not reference Authentik (single home writer)' >&2
  exit 1
fi

echo 'pantrybot-promote-site-test=passed'