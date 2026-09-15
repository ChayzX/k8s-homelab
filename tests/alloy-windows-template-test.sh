#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config="$root/observability/external-host/alloy-windows.alloy.example"

test -f "$config"
grep -q 'prometheus.exporter.windows "host"' "$config"
grep -q 'prometheus.remote_write "pantryprom"' "$config"
grep -q 'PANTRY_PROMETHEUS_REMOTE_WRITE_URL' "$config"
! grep -q 'grafana.net' "$config"
grep -q 'windows_cpu_time_total' "$config"
grep -q 'windows_memory_available_bytes' "$config"
grep -q 'enabled_collectors.*gpu' "$config"
grep -q 'windows_gpu_.*' "$config"
grep -q 'loki.source.windowsevent' "$config"
grep -q 'PANTRY_LOKI_PUSH_URL' "$config"
grep -q 'max_shards           = 2' "$config"
grep -q 'action = "keep"' "$config"
! grep -Eq 'glsa_|glc_|password = "[A-Za-z0-9_/-]{20,}"' "$config"
! grep -Eq 'regex = ".*(^|\\|)(process_|service_|container_|go_)' "$config"

echo 'alloy-windows-template-test=passed'
