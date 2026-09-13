#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configs=(
  "$root/observability/prometheus-config.yaml"
  "$root/observability/oracle-prometheus-config.yaml"
)

for config in "${configs[@]}"; do
  test -f "$config"
  if grep -q '^    remote_write:' "$config"; then
    echo "Prometheus metrics must remain on-prem; remote_write found: $config" >&2
    exit 1
  fi
  grep -q 'scrape_configs:' "$config" || {
    echo "local scrape configuration is missing: $config" >&2
    exit 1
  }
done

echo "grafana-cloud-budget-test=passed"
