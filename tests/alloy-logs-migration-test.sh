#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
legacy="pro""mtail"
test ! -e "$root/observability/${legacy}.yaml"
test ! -e "$root/observability/${legacy}-config.yaml"
test ! -e "$root/observability/oracle-${legacy}.yaml"
grep -q '^      - job_name: alloy-logs' "$root/observability/prometheus-config.yaml"
grep -q '^      - job_name: alloy-logs-oracle' "$root/observability/oracle-prometheus-config.yaml"
test -f "$root/observability/alloy-logs-home.yaml"
test -f "$root/observability/alloy-logs-oracle.yaml"
if rg -ni "$legacy" "$root" --glob '!*.git*' --glob '!tests/alloy-logs-migration-test.sh'; then
  echo 'retired collector references remain' >&2
  exit 1
fi
echo 'alloy-logs-migration-test=passed'
