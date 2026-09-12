# Grafana Cloud migration

Status: Cloud ingestion and dashboard migration are complete for the home
collector; local Grafana/Loki remain online during the validation window.

This migration changes only observability collectors and dashboard placement;
it does not touch PantryBot.

## Decision

Grafana Cloud is the shared observability UI and long-lived external store.
Each site continues collecting locally so a temporary internet outage does
not stop local scraping or log collection.

| Signal | Current path | Target path |
| --- | --- | --- |
| Metrics | Local Prometheus on MinecraftMachine | Local Prometheus at each site plus remote-write to Grafana Cloud Mimir |
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

The home Prometheus collector now remote-writes to the Cloud Prometheus
endpoint using the `grafana-cloud-metrics` Secret. The live verification
observed accepted samples and zero failed samples. The token is not stored in
the repository.

## Secret contract

The home cluster Secret currently contains these keys:

- `remote-write-url`
- `username`
- `password` (the `metrics:write` access-policy token)

The Prometheus manifest mounts the password at
`/etc/prometheus/secrets/password`; the endpoint and username are configured
in `prometheus-config.yaml`. Keep the token out of ConfigMaps, dashboards,
GitHub issues, and shell history.

## Cutover gates

1. Verify
   `prometheus_remote_storage_samples_pending` returns to zero.
2. Verify logs and metrics from the home site in Cloud Explore for one retention
   interval.
3. Add a separately deployed Oracle collector before claiming multi-site
   observability coverage; Oracle is not currently sending Prometheus metrics
   through this home Deployment.
4. Only then reduce local Grafana/Loki/Prometheus retention. Keep local
   collectors as an outage buffer and retain external monitoring/UptimeRobot.

The local Grafana UI remains available for rollback and comparison. Do not
decommission local Grafana or Loki until the validation window is complete.
