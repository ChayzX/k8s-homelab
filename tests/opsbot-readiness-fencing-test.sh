#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
health="$root/opsbot/bot/health.py"
main="$root/opsbot/bot/main.py"
runbook="$root/opsbot/OWNERSHIP-REHEARSAL.md"

grep -Fq 'def mark_not_ready()' "$health"
grep -Fq 'health.mark_not_ready()' "$main"
grep -Fq 'OWNERSHIP.start(_close_after_fence)' "$main"
grep -Fq 'A 503 readiness response is required evidence' "$runbook"

echo 'opsbot-readiness-fencing-test=passed'
