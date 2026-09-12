# Observability, Grafana Cloud, Prometheus, and Loki SOP

## Current topology and authority

The `observability` namespace contains local Grafana, Loki, Prometheus,
Promtail, kube-state-metrics, node exporters, the Oracle Prometheus collector,
and the failover-witness relay. The home Prometheus and Oracle Prometheus
remote-write to Grafana Cloud; Promtail sends logs to local Loki and Grafana
Cloud during the validation window. The local stores are not a replicated
multi-primary history database.

The host `k3s-watcher.service` is an independent alert path. Its source and
configuration are in `observability/k3s-watcher/`; it checks Loki, Kubernetes
workloads/nodes, and configured public routes. External monitoring remains
important because an in-cluster evaluator can disappear with the home site.

## Tracking and evidence

Record alert continuity, Grafana Cloud ingestion, duplicate suppression, and
route evidence in [homelab #203](https://github.com/ChayzX/k8s-homelab/issues/203).
Record cross-service capacity, free-tier usage, and RTO/RPO evidence in
[homelab #191](https://github.com/ChayzX/k8s-homelab/issues/191). Include the
alert fingerprint and UTC evidence; a healthy local collector does not close
an incident.

## Normal health check

```bash
kubectl -n observability get deploy,ds,sts,pods,svc,pvc -o wide
kubectl -n observability get events --sort-by=.lastTimestamp | tail -80
kubectl -n observability logs deploy/prometheus --since=30m --tail=150
kubectl -n observability logs deploy/prometheus-oracle --since=30m --tail=150
kubectl -n observability logs deploy/loki --since=30m --tail=150
sudo systemctl is-active k3s-watcher.service
sudo journalctl -u k3s-watcher.service --since='30 minutes ago' --no-pager
```

Use Prometheus `/api/v1/targets`, `/-/ready`, Loki `/ready`, and Grafana Cloud
Explore/API with credentials supplied through their configured Secret or local
environment. Never print Grafana Cloud tokens or webhook URLs.

## Read-only triage

Confirm the cluster and inspect both sites before restarting a collector or
silencing an alert:

```bash
kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
kubectl -n observability get deploy,ds,sts,pods,svc,pvc -o wide
kubectl -n observability get events --sort-by=.lastTimestamp | tail -80
```

Then query target labels, remote-write backlog, Loki readiness, Grafana Cloud
ingestion, and `k3s-watcher.service` logs without printing credentials. Missing
home data during a site/WAN event is not proof that Oracle collection or the
alert contract failed.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Deployment/DaemonSet gaps, probes, `up` series, remote-write backlog | Events, logs, target list, WAL/PVC status; identify home vs Oracle label before acting | Restart/roll back only the affected collector. Promtail can be restarted; preserve positions and local WAL unless corruption is proven. | Collector ready, targets healthy, remote-write pending queue drains, and Grafana Cloud receives fresh labeled samples/logs. |
| Node/site loss | Node condition, site management loss, node-exporter disappearance, or missing Promtail | Check which signals are lost and whether the other site's collector remains live; do not interpret missing data as outage without site labels | Recreate node-local DaemonSet work when the node returns. External checks and Cloud history remain authoritative; do not promote local history. | Both site labels observed after recovery, no duplicate alert storm, and gap duration recorded. |
| WAN partition | One site's remote-write/log path or public checks fail while local collectors may be healthy | Query Grafana Cloud, external monitors, both site-local readiness endpoints, and watcher state independently | Keep collection running where possible; do not run two alert evaluators or silence alerts without a dedupe decision. | Alerts still arrive through the surviving path, Oracle metrics/logs are visible, and no unbounded backlog after WAN return. |
| Oracle outage | Oracle-labeled targets/logs absent | Verify home collector and Cloud ingestion; check only Oracle resources | Keep home collection running; repair/redeploy Oracle collectors after node recovery. | Home and external observations remain healthy; Oracle gap is annotated. |
| Split-brain/duplicate alert evaluators | Repeated Discord alerts, two watcher processes, duplicate Grafana notifications | Compare `systemctl`, pod lists, alert fingerprints, cooldown state, and watcher logs. Do not suppress all errors to quiet symptoms. | Keep one designated host watcher/evaluator per alert contract; stop duplicate instance and preserve cooldown state. Cloudflared readiness suppression applies only to exact known healthy connector messages. | One alert per incident/cooldown, real readiness/origin errors still alert, and controlled test reaches both Discord/Operations paths if configured. |
| TSDB/Loki/PVC corruption | Prometheus mmap/WAL errors, Loki index/chunk errors, Grafana panels empty | Stop or scale the affected stateful process only after recording logs/PVC; query Grafana Cloud before deleting local history | Restore local data from its backup/retention procedure or recreate a bounded local store. Grafana Cloud is the long-lived source for migrated data, not proof that local history can be rebuilt automatically. | Cloud queries return fresh data, local `/-/ready` returns, and dashboard selectors show the intended site/namespace. |
| Secret/certificate failure | Remote-write 401/403, Loki 401/403, webhook/TLS errors, Cloudflare auth failure | Inspect Secret names/key names, response status, endpoint, and certificate expiry without printing values | Recreate the named Secret from approved provider access, restart the affected collector/watcher, and roll back the Secret/config version if ingestion fails. | Cloud sample/log arrives with correct labels, alert notification test passes, no token in logs/issues. |
| Routing/tunnel failure | Grafana/Operations protected route fails while pods ready; Cloudflare connector errors | Test local Service, connector `/ready`, external URL, and Grafana Cloud separately. Check for stale origin/endpoints. | Restore documented tunnel origin or temporary NodePort verification path; do not expose Loki/Prometheus publicly. Roll back route config. | External Grafana login/data, local probes, and Cloud Explore all work; local-only success is insufficient. |
| Resource exhaustion | PVC full, WAL backlog, OOM/eviction, Grafana Cloud usage near free-tier limits | `kubectl top`, PVC usage, Prometheus queue metrics, Cloud usage; do not delete WAL blindly | Reduce scrape/log volume, bound retention, or pause nonessential local history. Keep Cloud remote-write and external checks prioritized. | Queues drain, no dropped samples beyond documented window, and 10k-series/50GB-log/retention assumptions are rechecked against account usage. |
| Rollback/failback | New dashboard/config/image causes no-data or alert regression | Compare tracked config/dashboards with last known-good and query raw data before editing panels | Reapply prior tracked ConfigMap/image, restart only affected deployment, and restore watcher env/service from prior version. Cloud history is not rolled back by changing a dashboard. | Raw Prometheus/Loki queries, dashboard panels, alert cooldown behavior, and external routes pass. |

## Alert-storm procedure

1. Confirm the alert fingerprint, namespace/pod, and whether it is a real
   readiness/origin failure.
2. Inspect the exact connector and Service endpoints. A stale Cloudflare origin
   to a retired Service can repeatedly emit connection-refused errors.
3. If the watcher itself is producing a storm, stop only the watcher process
   after recording its journal, then fix the route/state and restart it:

```bash
sudo systemctl stop k3s-watcher.service
sudo journalctl -u k3s-watcher.service --since='30 minutes ago' --no-pager
# perform the reviewed configuration/route correction
sudo systemctl start k3s-watcher.service
sudo systemctl is-active k3s-watcher.service
```

Do not disable all alerting or suppress generic timeout, DNS, dial, or origin
errors. Run `PYTHONPATH=observability/k3s-watcher python3 observability/k3s-watcher/test_watcher.py`
after source changes.

## Promotion and failback gates

Observability has active collector capacity at both sites, but local Prometheus,
Loki, and Grafana history is not multi-primary. Promotion means continuing the
Cloud collection path and, only where the alert contract permits, selecting one
watcher/evaluator owner; it does not promote a local history database. Prove
the surviving site, external monitoring, Cloud ingestion, exact dedupe state,
and stale-evaluator shutdown before changing ownership. Failback restores the
original owner only after the replacement collector is healthy and its
duplicate path is stopped.

## Verification checklist

Record in #203 and #191: labeled samples/logs from each available site;
Prometheus/Loki readiness and target state; Grafana Cloud raw-query evidence;
remote-write convergence; one controlled alert with no duplicate; public-route
observation; capacity/free-tier impact; and rollback/failback behavior. A green
pod, empty dashboard, or quiet Discord channel alone is not verification.

## Known gates

- Grafana Cloud is the external observation path, not a replacement for local
  buffers or UptimeRobot/GCP checks.
- Local Prometheus/Loki histories are not cross-site replicated stores.
- Alert delivery while the home cluster and Authentik are unavailable is the
  promotion evidence; healthy collectors alone are not.
