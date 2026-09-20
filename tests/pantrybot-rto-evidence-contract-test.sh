#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT/docs/recovery/rehearse-pantrybot-postgres-authority.sh"

# The disposable setup duration must never be reported as production RTO/RPO.
grep -Fq '"setup_seconds": int(finished) - int(started)' "$SCRIPT"
grep -Fq '"rto_seconds": None' "$SCRIPT"
grep -Fq '"rpo_seconds": None' "$SCRIPT"
grep -Fq '"measurement_status": "not_measured"' "$SCRIPT"
if grep -Fq '"rto_seconds": int(finished) - int(started)' "$SCRIPT"; then
  echo 'pantrybot-rto-evidence-contract-test=failed' >&2
  exit 1
fi

echo 'pantrybot-rto-evidence-contract-test=passed'
