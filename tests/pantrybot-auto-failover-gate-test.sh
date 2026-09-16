#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT/scripts/pantrybot-auto-failover-oracle.sh"

if "$SCRIPT" --confirm >/dev/null 2>&1; then
  echo 'automatic failover must refuse missing configuration' >&2
  exit 1
fi

output=$(HOME_PRIMARY_PROBE_COMMAND='/usr/local/sbin/probe-home-primary --expect-primary' \
  ORACLE_PRIMARY_PROBE_COMMAND='/usr/local/sbin/probe-oracle-primary' \
  HOME_FAILURE_THRESHOLD=3 \
  HOME_PROBE_INTERVAL_SECONDS=5 \
  WITNESS_URL=http://127.0.0.1:8765 WITNESS_SHARED_SECRET=test \
  OLD_WRITER_FENCE_COMMAND='/usr/local/sbin/fence-home --confirm' \
  PROMOTION_COMMAND='/usr/local/sbin/pantrybot-promote-site.sh --confirm' \
  "$SCRIPT" --dry-run)

grep -Fq 'probe_source=private-home-primary-command' <<<"$output"
grep -Fq 'failure_threshold=3' <<<"$output"
grep -Fq 'promotion_command_validated' <<<"$output"
grep -Fq 'automatic_promotion=disabled' <<<"$output"

grep -Fq 'HOME_PRIMARY_PROBE_URL' "$SCRIPT"
grep -Fq 'HOME_PRIMARY_PROBE_COMMAND' "$SCRIPT"
grep -Fq 'ORACLE_PRIMARY_PROBE_COMMAND' "$SCRIPT"
grep -Fq 'HOME_FAILURE_THRESHOLD' "$SCRIPT"
grep -Fq 'PROMOTION_COMMAND' "$SCRIPT"
grep -Fq 'oracle_already_primary' "$SCRIPT"
grep -Fq 'pantrybot-promote-site' "$SCRIPT"
grep -Fq 'curl --fail' "$SCRIPT"
grep -Fq 'probe_command' "$SCRIPT"

# The supervisor must no longer hard-require the Authentik home fence; forced
# Auth promotion is not part of automatic failover (single home writer).
if grep -Fq 'AUTH_HOME_FENCE_COMMAND:?' "$SCRIPT"; then
  echo 'automatic failover must not require the Auth home fence' >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat >"$tmp/oracle-primary" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$tmp/home-primary" <<EOF
#!/usr/bin/env bash
touch "$tmp/home-probed"
exit 1
EOF
chmod +x "$tmp/oracle-primary" "$tmp/home-primary"
output=$(HOME_PRIMARY_PROBE_COMMAND="$tmp/home-primary" \
  ORACLE_PRIMARY_PROBE_COMMAND="$tmp/oracle-primary" \
  HOME_FAILURE_THRESHOLD=3 \
  HOME_PROBE_INTERVAL_SECONDS=1 \
  WITNESS_URL=http://127.0.0.1:8765 WITNESS_SHARED_SECRET=test \
  OLD_WRITER_FENCE_COMMAND='/usr/local/sbin/fence-home --confirm' \
  PROMOTION_COMMAND='/usr/local/sbin/pantrybot-promote-site.sh --confirm' \
  AUTO_FAILOVER_LOCK_PATH="$tmp/lock" \
  "$SCRIPT" --confirm)
grep -Fq 'oracle_already_primary' <<<"$output"
test ! -e "$tmp/home-probed"

echo 'pantrybot-auto-failover-gate-test=passed'