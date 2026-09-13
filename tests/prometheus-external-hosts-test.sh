#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config="$root/observability/prometheus-config.yaml"

grep -q 'file_sd_configs:' "$config"
grep -q '/etc/prometheus/external-hosts.yml' "$config"
grep -q '^  external-hosts.yml: |' "$config"
grep -q 'job_name: external-host' "$config"
grep -q 'site: home' "$config"

echo 'prometheus-external-hosts-test=passed'
