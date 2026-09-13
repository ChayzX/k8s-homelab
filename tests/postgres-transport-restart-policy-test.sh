#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
units=(
  "$root/observability/failover-witness/pantry-bot-postgres-home-tunnel.service"
  "$root/observability/failover-witness/pantry-bot-postgres-home-reverse-forward.service"
  "$root/observability/failover-witness/pantry-bot-postgres-oracle-reverse.service"
)

for unit in "${units[@]}"; do
  test -f "$unit"
  grep -q '^StartLimitIntervalSec=' "$unit"
  grep -q '^StartLimitBurst=' "$unit"
  grep -q '^Restart=on-failure$' "$unit"
  grep -q '^RestartSec=30$' "$unit"
done

echo 'postgres-transport-restart-policy-test=passed'
