# Grafana Cloud migration

Status: Cloud ingestion and dashboard migration are complete for the home and
Oracle collectors; local Grafana/Loki remain online during the validation
window.

This migration changes only observability collectors and dashboard placement;
it does not touch PantryBot.

## Decision

Grafana Cloud is the shared observability UI and long-lived external store.
Each site continues collecting locally so a temporary internet outage does
not stop local scraping or log collection.

| Signal | Current path | Target path |
| --- | --- | --- |
| Metrics | Local Prometheus on MinecraftMachine | Local Prometheus at each site plus an allowlisted remote-write stream to Grafana Cloud Mimir |
| Logs | Promtail to local Loki and Grafana Cloud | Promtail dual-write during validation, then Cloud-first with bounded local retention |
| Dashboards | Local Grafana PVC | Import the tracked `dashboards/*.json` files into Grafana Cloud |
| External checks | GCP monitor/UptimeRobot | Retained; Grafana Cloud is not the only failure detector |

Grafana Cloud's current free plan lists 10,000 active metric series, 50 GB
of log ingestion per month, and 14-day retention. Actual usage still needs
to be measured before removing local retention. See the official
[metrics remote-write guide](https://grafana.com/docs/grafana-cloud/observe-and-act/send-data/metrics/metrics-prometheus/prometheus-config-examples/integration-guide/)
and [current pricing](https://grafana.com/pricing/).

## Completed

`observability/promtail-config.yaml` already sends logs to both the local Loki
service and the Grafana Cloud Loki endpoint. The credential is mounted from
the `grafana-cloud-loki` Secret and is not committed. Keep this dual-write
until Cloud Explore verifies logs from MinecraftMachine and Oracle.

The tracked dashboards are portable JSON under `dashboards/`; no local
Grafana database migration is required for the dashboard definitions.

Grafana Cloud now contains the homelab dashboards, with their datasource
variables mapped to the managed `grafanacloud-prom` and `grafanacloud-logs`
datasources. The temporary migrated `Prometheus` and `Loki` datasources that
pointed at in-cluster URLs were removed after confirming no dashboard or alert
rule referenced them.

The home and Oracle Prometheus collectors now remote-write to the Cloud
Prometheus endpoint using site-local `grafana-cloud-metrics` Secrets. The
local collectors retain their complete scrape sets, while
`write_relabel_configs` exports only health, capacity, application, and
required dashboard/Minecraft metric families. Full kubelet and cAdvisor
high-cardinality series remain available locally but are not sent to the free
Cloud stack. Live verification observed approximately 7,002 selected home
series and 990 selected Oracle series, with remote-write rates around 478 and
64 samples/sec respectively and zero failed samples. The token is not stored
in the repository. This is the `metrics:write` credential used by Prometheus;
it is not a Grafana Cloud query credential.

## Secret contract

The current Prometheus manifests require the home and Oracle
`grafana-cloud-metrics` Secrets to contain this key:

- `password` (the `metrics:write` access-policy token)

The Prometheus manifest mounts the password at
`/etc/prometheus/secrets/password`; the endpoint and username are configured
in the respective Prometheus ConfigMaps. Keep the token out of ConfigMaps,
dashboards, GitHub issues, and shell history.

This Secret contract is only for Prometheus remote write. A
`metrics:read` credential, when needed for Grafana Cloud Explore, dashboards,
or API queries, is separate from this Secret and must not be substituted for
the `metrics:write` token.

The query endpoint is provided by the Grafana Cloud portal for the stack's
Prometheus data source. Copy that portal-provided endpoint when configuring a
reader; do not infer it from the remote-write URL or from
`/api/prom/push`. The `/api/prom/push` path in this repository is a write-only
destination for Prometheus.

## Cutover gates

1. Verify each collector's
   `prometheus_remote_storage_samples_pending` queue drains after its initial
   WAL replay. A non-zero transient queue is expected during catch-up, but a
   sustained increase or any non-zero
   `prometheus_remote_storage_samples_failed_total` requires investigation.
2. Verify logs and metrics from the home site in Cloud Explore for one retention
   interval.
3. Oracle has a separately deployed, Oracle-labeled Prometheus collector using
   its own 8Gi local buffer, with node-exporter, kube-state-metrics, kubelet,
   and cAdvisor targets healthy. Its allowlisted remote-write queue is active
   with zero failed samples; continue monitoring convergence before reducing
   local retention.
4. Only then reduce local Grafana/Loki/Prometheus retention. Keep local
   collectors as an outage buffer and retain external monitoring/UptimeRobot.

The local Grafana UI remains available for rollback and comparison. Do not
decommission local Grafana or Loki until the validation window is complete.
