#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/authentik-postgres-backup.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -x "$SCRIPT" ]] || fail "backup script is not executable"
bash -n "$SCRIPT" || fail "backup script has invalid shell syntax"

# Large stdin streams through kubectl exec were the observed hang risk. The
# transfer must use kubectl cp and the rclone operation must be a separate
# bounded command.
grep -Eq 'timeout .* kubectl cp ' "$SCRIPT" || fail "missing bounded kubectl cp transfer"
if grep -Eq '^[[:space:]]*[^#].*kubectl exec -i' "$SCRIPT"; then
  fail "unbounded exec stdin transfer remains"
fi

grep -q -- '--s3-no-check-bucket' "$SCRIPT" || fail "missing scoped-bucket upload flag"
grep -q 'rclone check' "$SCRIPT" || fail "missing remote hash verification"
grep -q 'rclone size' "$SCRIPT" || fail "missing exact remote size verification"
grep -q -- '--json' "$SCRIPT" || fail "missing JSON remote size verification"
grep -q -- '--for=condition=Ready' "$SCRIPT" || fail "missing sidecar readiness gate"

local_line="$(grep -n 'mv "\$tmp" "\$local_path"' "$SCRIPT" | cut -d: -f1)"
r2_line="$(grep -n '^R2_POD=' "$SCRIPT" | cut -d: -f1)"
[[ -n "$local_line" && -n "$r2_line" && "$local_line" -lt "$r2_line" ]] \
  || fail "local recovery point is not finalized before remote upload"

echo 'authentik-backup-script-test=passed'
