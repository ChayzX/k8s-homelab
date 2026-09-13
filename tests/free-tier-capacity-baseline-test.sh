#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
doc="$root/docs/recovery/FREE-TIER-CAPACITY-BASELINE.md"

grep -Fq '1,500 OCPU-hours and 9,000' "$doc"
grep -Fq '2 total OCPUs and 12 GB' "$doc"
grep -Fq 'scheduled requests are 1,900m CPU (95% of allocatable)' "$doc"
grep -Fq 'No PantryBot application or database pod is currently' "$doc"
grep -Fq 'Oracle is the writable database authority' "$doc"
if grep -Fq '3,000 OCPU-hours and 18,000' "$doc"; then
  echo 'free-tier baseline contains obsolete Oracle allowance' >&2
  exit 1
fi

echo 'free-tier-capacity-baseline-test=passed'
