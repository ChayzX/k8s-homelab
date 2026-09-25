#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configs=(
  "$root/observability/prometheus-config.yaml"
  "$root/observability/oracle-prometheus-config.yaml"
)

pantry_roles=(api gateway worker dispatcher)

for config in "${configs[@]}"; do
  test -f "$config"
  if [[ "$config" == */oracle-prometheus-config.yaml ]]; then
    site=oracle
    suffix=-oracle
  else
    site=home
    suffix=
  fi

  for role in "${pantry_roles[@]}"; do
    job="pantry-bot-${role}${suffix}"
    grep -q "^      - job_name: ${job}$" "$config" || {
      echo "Missing Pantry Bot role ${job} in $config" >&2
      exit 1
    }
    block=$(sed -n "/^      - job_name: ${job}$/,/^      - job_name:/p" "$config")
    grep -q 'metrics_path: /metrics' <<<"$block" || {
      echo "Missing /metrics path for ${job} in $config" >&2
      exit 1
    }
    grep -q "site: ${site}" <<<"$block" || {
      echo "Missing site: ${site} label for ${job} in $config" >&2
      exit 1
    }
  done

  # The failover witness was retired with the three-site design on 2026-09-25
  # (ChayzX/pantry-bot#315). This assertion is inverted deliberately: it used to
  # require the witness job to exist, which meant the test failed *because* the
  # migration succeeded. Now it fails if the witness scrape target comes back.
  ! grep -q 'pantry-postgres-witness' "$config" || {
    echo "Retired failover witness scrape job found in $config" >&2
    exit 1
  }
  ! grep -q 'failover-witness-relay' "$config" || {
    echo "Retired failover witness relay target found in $config" >&2
    exit 1
  }

  ! grep -q 'site: canada' "$config" || {
    echo "Stale Canada target found in $config" >&2
    exit 1
  }
  ! grep -q '192\.168\.40\.208:13101' "$config" || {
    echo "Stale Canada target address found in $config" >&2
    exit 1
  }
  grep -q '^      - job_name: alloy-logs' "$config" || {
    echo "Alloy scrape target missing in $config" >&2
    exit 1
  }
done

echo "pantrybot-observability-config-test=passed"
