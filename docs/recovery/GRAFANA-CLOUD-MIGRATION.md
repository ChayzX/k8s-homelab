# Grafana Cloud migration

Status: feasible; staged, not yet cut over.

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

## Already complete

`observability/promtail-config.yaml` already sends logs to both the local Loki
service and the Grafana Cloud Loki endpoint. The credential is mounted from
the `grafana-cloud-loki` Secret and is not committed. Keep this dual-write
until Cloud Explore verifies logs from MinecraftMachine and Oracle.

The tracked dashboards are portable JSON under `dashboards/`; no local
Grafana database migration is required for the dashboard definitions.

## Remaining operator input

Create a Grafana Cloud access policy with `metrics:write`, then obtain these
non-secret values from the stack's Prometheus details page:

- Prometheus remote-write URL, such as `https://prometheus-prod-<region>.grafana.net/api/prom/push`
- Prometheus instance/user ID

Keep the access-policy token secret. Create this Secret separately in both the
home and Oracle `observability` namespaces:

```bash
kubectl -n observability create secret generic grafana-cloud-metrics \
  --from-literal=grafana-cloud-metrics-password='<access-policy-token>'
```

The migration will mount that key as
`/etc/prometheus/secrets/grafana-cloud-metrics-password` and use Prometheus'
native `remote_write` queue. Do not put the token in a ConfigMap, dashboard,
GitHub issue, or shell history.

## Cutover gates

1. Add the remote-write block and password-file mount to both site Prometheus
   manifests, with distinct external labels (`site=home` and `site=oracle`).
2. Apply to one site at a time and verify
   `prometheus_remote_storage_samples_pending` returns to zero.
3. Import the tracked dashboards and point them at the Cloud Prometheus and
   Loki data sources.
4. Verify logs and metrics from both sites in Cloud Explore for one retention
   interval.
5. Only then reduce local Grafana/Loki/Prometheus retention. Keep local
   collectors as an outage buffer and retain external monitoring/UptimeRobot.

## Current blocker

The repository has the Grafana Cloud Loki credential but no metrics endpoint
or metrics token. The migration is prepared, but remote-write configuration is
intentionally not applied until those values exist.
