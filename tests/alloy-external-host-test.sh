#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config="$root/observability/external-host/alloy-linux.alloy.example"
windows_config="$root/observability/external-host/alloy-windows.alloy.example"
readme="$root/observability/external-host/README.md"

test -f "$config"
test -f "$windows_config"
grep -q 'prometheus.exporter.unix "host"' "$config"
grep -q 'prometheus.remote_write "grafana_cloud"' "$config"
grep -q 'password_file = "/etc/alloy/grafana-cloud-token"' "$config"
grep -q 'write_relabel_config' "$config"
grep -q 'node_cpu_seconds_total' "$config"
grep -q 'node_memory_MemAvailable_bytes' "$config"
grep -q 'No inbound' "$readme"
! grep -Eq 'glsa_|glc_|password = "[A-Za-z0-9_/-]{20,}"' "$config"
grep -q 'prometheus.exporter.windows "host"' "$windows_config"
grep -q 'windows_cpu_time_total' "$windows_config"
grep -q 'windows_memory_available_bytes' "$windows_config"
grep -q 'password_file = "C:\\\\ProgramData\\\\GrafanaLabs\\\\Alloy\\\\grafana-cloud-token"' "$windows_config"
! grep -Eq 'glsa_|glc_|password = "[A-Za-z0-9_/-]{20,}"' "$windows_config"

echo 'alloy-external-host-test=passed'
