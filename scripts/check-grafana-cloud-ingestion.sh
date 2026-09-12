#!/usr/bin/env bash
set -euo pipefail

# Verify site-local Prometheus remote-write health without a Grafana Cloud
# query credential. The configured credential is metrics:write; Cloud
# Explore/query verification is a separate human/API gate.

namespace=${OBSERVABILITY_NAMESPACE:-observability}
kubectl_bin=${KUBECTL_BIN:-kubectl}

pod=$(
  "$kubectl_bin" -n "$namespace" get pod \
    -l app.kubernetes.io/name=prometheus \
    -o jsonpath='{.items[0].metadata.name}'
)
if [[ -z "$pod" ]]; then
  echo "no Prometheus pod found in namespace $namespace" >&2
  exit 1
fi

query() {
  local expression=$1
  local encoded
  encoded=$(jq -rn --arg value "$expression" '$value|@uri')
  "$kubectl_bin" -n "$namespace" exec "$pod" -- \
    wget -qO- "http://127.0.0.1:9090/api/v1/query?query=$encoded"
}

sum_metric() {
  jq -r '[.data.result[]?.value[1] | tonumber] | add // 0'
}

failed=$(query prometheus_remote_storage_samples_failed_total | sum_metric)
pending=$(query prometheus_remote_storage_samples_pending | sum_metric)
shards=$(query prometheus_remote_storage_shards | sum_metric)

printf 'grafana_cloud_remote_write_failed=%s\n' "$failed"
printf 'grafana_cloud_remote_write_pending=%s\n' "$pending"
printf 'grafana_cloud_remote_write_shards=%s\n' "$shards"

if [[ "$failed" != "0" ]]; then
  echo "remote-write failures detected; inspect Prometheus logs and Cloud credentials" >&2
  exit 1
fi

echo 'grafana-cloud-ingestion-check=passed'
