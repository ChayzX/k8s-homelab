#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dashboard="$root/dashboards/truffles-streaming.json"
gateway="$root/observability/prometheus-write-gateway.yaml"
tunnel="$root/observability/windows-alloy-metrics-cloudflared.yaml"
workflow="$root/.github/workflows/windows-alloy-metrics-deploy.yml"
grafana_workflow="$root/.github/workflows/grafana-deploy.yml"

test -f "$dashboard"
test -f "$gateway"

jq -e '
  .uid == "truffles-streaming" and
  .title == "TRUFFLES Streaming" and
  ([.panels[]?.targets[]?.datasource?.uid] | map(select(. != null)) | all(. == "Prometheus")) and
  ([.panels[]?.targets[]?.expr] | map(select(. != null)) | all(test("instance=\\\"TRUFFLES\\\"") and test("role=\\\"streaming-laptop\\\""))) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("streaming_gpu_encoder_utilization_ratio")) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("streaming_gpu_temperature_celsius")) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("streaming_obs_stream_active")) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("streaming_obs_render_skipped_frames_total")) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("streaming_obs_network_dropped_frames_total")) and
  ([.panels[]?.targets[]?.expr] | join("\n") | test("windows_process_cpu_time_total.*obs64.*VTubeControl"))
' "$dashboard" >/dev/null

mapfile -t gateway_objects < <(kubectl create --dry-run=client -f "$gateway" -o name)
test "${gateway_objects[*]}" = "configmap/prometheus-write-gateway service/prometheus-origin"

grep -q 'prometheus.observability.svc.cluster.local' "$tunnel"
grep -q 'ip: "127.0.0.1"' "$tunnel"
grep -q 'name: write-gateway' "$tunnel"
grep -q 'image: nginxinc/nginx-unprivileged:1.27-alpine' "$tunnel"

grep -q 'kubectl apply -f observability/prometheus-write-gateway.yaml' "$workflow"
! grep -q 'rollout status deployment/prometheus-write-gateway' "$workflow"
grep -q 'kubectl apply --server-side -f dashboards/dashboards-configmap.yaml' "$grafana_workflow"

echo 'truffles-streaming-dashboard-test=passed'
