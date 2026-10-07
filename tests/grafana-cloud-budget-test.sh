#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configs=("$root/observability/prometheus-config.yaml")

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

oracle="$root/observability/oracle-prometheus-config.yaml"
test -f "$oracle"
grep -q 'url: https://metrics.greeniespantry.uk/api/v1/write' "$oracle"
grep -q 'kube_cronjob_status_last_successful_time;kube-state-metrics-oracle;pantry-bot;postgres-backup' "$oracle"
test "$(grep -c '^      - url:' "$oracle")" -eq 1
! grep -q 'grafana.net' "$oracle"
grep -q 'scrape_configs:' "$oracle"

echo "grafana-cloud-budget-test=passed"
