# Grafana Cloud migration and on-prem rollback

Status: on-prem Grafana is restored as the active UI and local Prometheus is
the authoritative metrics store. A bounded Cloud Loki log copy remains an
optional diagnostic path; metric remote-write has been disabled.

This migration changes only observability collectors and dashboard placement;
it does not touch PantryBot.

## Decision

The self-hosted Grafana instance is the active observability UI and local
Prometheus/Loki are the primary data stores. Cloud is retained only for the
explicitly selected log copy and must not receive the full metrics stream.
Each site continues collecting locally so an internet outage does not stop
local dashboards or log collection.

| Signal | Current path | Target path |
| --- | --- | --- |
| Metrics | Local Prometheus on MinecraftMachine | Local Prometheus at each site; no Grafana Cloud remote-write stream |
| Logs | Alloy to local Loki | Grafana Cloud log copy remains an optional, separately enabled follow-up |
| Dashboards | Local Grafana PVC | Tracked `dashboards/*.json` files provisioned into on-prem Grafana |
| External checks | GCP monitor/UptimeRobot | Retained; Grafana Cloud is not the only failure detector |

Grafana Cloud's current free plan lists 10,000 active metric series, 50 GB
of log ingestion per month, and 14-day retention. Actual usage still needs
to be measured before removing local retention. See the official
[metrics remote-write guide](https://grafana.com/docs/grafana-cloud/observe-and-act/send-data/metrics/metrics-prometheus/prometheus-config-examples/integration-guide/)
and [current pricing](https://grafana.com/pricing/).

## Completed

`observability/alloy-logs-*.yaml` currently sends logs only to the local Loki
service. Grafana Cloud log forwarding is not enabled in the live manifests;
the credential contract remains documented for a future, deliberate
dual-write experiment.

The tracked dashboards are portable JSON under `dashboards/`; no local
Grafana database migration is required for the dashboard definitions.

Grafana Cloud may contain historical homelab dashboards, with their datasource
variables mapped to the managed `grafanacloud-prom` and `grafanacloud-logs`
datasources. The temporary migrated `Prometheus` and `Loki` datasources that
pointed at in-cluster URLs were removed after confirming no dashboard or alert
rule referenced them.

The home and Oracle Prometheus collectors retain metrics locally and no
longer mount or use the `grafana-cloud-metrics` Secret. This is the deliberate
on-prem cutover and stops metric ingestion charges. Alloy currently keeps
local Loki available for dashboards and local incident response.

## Secret contract

The current Prometheus manifests do not require the old
`grafana-cloud-metrics` Secret. It may be removed after confirming no other
collector uses it. The optional log archive remains separately scoped to the
`grafana-cloud-loki` Secret.

No metrics token is needed for the on-prem path. Keep any retained log token
out of ConfigMaps, dashboards, GitHub issues, and shell history.

The query endpoint is provided by the Grafana Cloud portal for the stack's
Prometheus data source. Copy that portal-provided endpoint when configuring a
reader; do not infer it from the remote-write URL or from
`/api/prom/push`. The `/api/prom/push` path in this repository is a write-only
destination for Prometheus.

## Cutover gates

1. Verify both local collectors are Ready and their `/api/v1/query` endpoint
   returns current `up` and node metrics.
2. Verify the local Grafana Prometheus and Loki datasources and a current
   dashboard query.
3. Verify retained Cloud logs only if that diagnostic copy is desired; a
   Cloud log outage must not make local Grafana unhealthy.
4. Keep local collectors and retention as the primary outage buffer and retain
   external monitoring/UptimeRobot.

The local Grafana UI remains available for rollback and comparison. Do not
decommission local Grafana or Loki until the validation window is complete.
