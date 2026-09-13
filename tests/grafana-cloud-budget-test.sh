#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configs=(
  "$root/observability/prometheus-config.yaml"
  "$root/observability/oracle-prometheus-config.yaml"
)

for config in "${configs[@]}"; do
  test -f "$config"
  grep -q '^    remote_write:' "$config" || {
    echo "missing remote_write config: $config" >&2
    exit 1
  }
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

  # The Cloud stream is intentionally allowlisted. These broad families stay
  # local because their per-process/container/service labels can exhaust the
  # free-tier series budget.
  if grep -Eq "^            regex: '.*(^|\\|)(container_|kubelet_|cadvisor_|process_|go_).*'" "$config"; then
    echo "remote-write allowlist includes a broad high-cardinality family: $config" >&2
    exit 1
  fi
done

grep -q 'cluster: minecraftmachine' "${configs[0]}"
grep -q 'node: minecraftmachine' "${configs[0]}"
grep -q 'cluster: pantry-bot-oracle' "${configs[1]}"
grep -q 'node: pantry-bot-oracle' "${configs[1]}"
grep -q 'site: oracle' "${configs[1]}"

echo "grafana-cloud-budget-test=passed"
