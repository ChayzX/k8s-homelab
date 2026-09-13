#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
unit="$root/observability/failover-witness/pantrybot-auto-failover-oracle.service"
timer="$root/observability/failover-witness/pantrybot-auto-failover-oracle.timer"
for wrapper in fence-home-pantry-via-gcp.sh fence-home-auth-via-gcp.sh; do
  test -x "$root/observability/failover-witness/$wrapper"
done
test -f "$unit"
test -f "$timer"
grep -q 'pantrybot-auto-failover-oracle.sh --confirm' "$unit"
grep -q '/etc/pantrybot/auto-failover.env' "$unit"
grep -q '^Type=oneshot$' "$unit"
grep -q '^OnBootSec=' "$timer"
grep -q '^Persistent=true$' "$timer"
grep -q '^Unit=pantrybot-auto-failover-oracle.service$' "$timer"
grep -q 'gcp-fence-home-writer.sh' "$root/observability/failover-witness/fence-home-pantry-via-gcp.sh"
grep -q 'gcp-fence-home-auth-writer.sh' "$root/observability/failover-witness/fence-home-auth-via-gcp.sh"

echo 'pantrybot-auto-failover-service-test=passed'
