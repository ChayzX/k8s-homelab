#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configs=(
  "$root/observability/prometheus-config.yaml"
  "$root/observability/oracle-prometheus-config.yaml"
)

for config in "${configs[@]}"; do
  test -f "$config"
  grep -q '^        write_relabel_configs:' "$config" || {
    echo "missing write_relabel_configs: $config" >&2
    exit 1
  }
  grep -q "action: keep" "$config" || {
    echo "missing remote-write keep policy: $config" >&2
    exit 1
  }
  for metric in up pantry_ prometheus_ kube_pod_status_phase; do
    grep -q "$metric" "$config" || {
      echo "missing required metric family $metric: $config" >&2
      exit 1
    }
  done
done

echo "grafana-cloud-budget-test=passed"
