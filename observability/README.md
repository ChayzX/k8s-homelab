# observability namespace

Kubernetes manifests for the `observability` namespace of the single-node
k3s home-lab migration (loki, promtail, prometheus, grafana,
kube-state-metrics). Ported from
`/home/chase/docker/observability/docker-compose.yml` per
`~/.claude/plans/i-d-love-to-run-cuddly-flurry.md`.

Node LAN IP: `192.168.40.208`. StorageClass: `local-path`.

## Files

| File | What it is |
|---|---|
| `namespace.yaml` | The `observability` Namespace (PSA `enforce: privileged` -- see the comment in the file for why promtail needs that). |
| `loki.yaml` | Loki SA, PVC (20Gi), Deployment, ClusterIP Service (3100). |
| `loki-config.yaml` | ConfigMap: Loki's `loki-config.yaml`. |
| `loki-external-nodeport.yaml` | **Migration-window only.** NodePort 31100 so the old Docker-side promtail can keep pushing to the new Loki. |
| `prometheus.yaml` | Prometheus SA, ClusterRole/Binding (API access for `kubernetes_sd_configs`), PVC (20Gi), Deployment, ClusterIP Service (9090). |
| `prometheus-config.yaml` | ConfigMap: Prometheus' `prometheus.yml`, rewritten scrape config. |
| `grafana.yaml` | Grafana SA, PVC (2Gi), Deployment, primary `LoadBalancer` Service (3002). |
| `grafana-provisioning.yaml` | ConfigMaps: `grafana-provisioning-datasources`, `grafana-provisioning-dashboards`, and the retained `grafana-provisioning-alerting` definition. **See "Grafana dashboard ConfigMap" below before applying.** |
| `grafana-verify-nodeport.yaml` | **Verification only.** NodePort 30002. |
| `promtail.yaml` | promtail SA, ClusterRole/Binding (API access for pod discovery), DaemonSet, ClusterIP Service (9080, metrics only). |
| `promtail-config.yaml` | ConfigMap: promtail's config, incl. severity-extraction `pipeline_stages`. |
| `kube-state-metrics.yaml` | kube-state-metrics SA, ClusterRole/Binding, Deployment, headless Service (8080/8081). |

Every stateful app (loki, prometheus, grafana) is a
`Deployment` with `strategy: Recreate` and a `ReadWriteOnce` PVC -- never
a StatefulSet, never `RollingUpdate`. promtail is a DaemonSet (no PVC).
kube-state-metrics is a stateless `Deployment` with ordinary
`RollingUpdate` (no PVC, nothing to lose during a rollout).

## Grafana dashboard ConfigMap -- ownership boundary

Two different agents touch Grafana's ConfigMaps and it's worth being
explicit about the split, since it's easy to end up with two conflicting
`provisioning/datasources` or `provisioning/dashboards` ConfigMaps:

- **This directory (`observability/`) owns the *provisioning*
  ConfigMaps** -- `grafana-provisioning-datasources`,
  `grafana-provisioning-dashboards`, and `grafana-provisioning-alerting`,
  all defined in `grafana-provisioning.yaml`. These are mounted by
  `grafana.yaml` at
  `/etc/grafana/provisioning/{datasources,dashboards}`; the alerting definition
  is retained but currently not mounted until its Secret is restored. This is the
  wiring the migration plan calls out explicitly (rev. 2, "Grafana
  manifest dropped the `dashboards` mount that the provisioning config
  references") -- getting it wrong yields a Grafana with zero
  dashboards and no obvious error, so treat these ConfigMaps as
  authoritative and don't duplicate them elsewhere.

- **The dashboard content is owned by `/home/chase/k8s-homelab/dashboards/`.**
  That directory contains the raw dashboard JSON and the generated
  `dashboards-configmap.yaml`. `grafana.yaml` mounts the generated ConfigMap
  named **`grafana-dashboards`** at `/etc/grafana/dashboards`.
  Regenerate it only when a source dashboard changes, then validate it with
  `scripts/validate-grafana-dashboards.sh`.

  ```bash
  kubectl -n observability create configmap grafana-dashboards \
    --from-file=/home/chase/k8s-homelab/dashboards/cluster-overview.json \
    --from-file=/home/chase/k8s-homelab/dashboards/pods-and-workloads.json \
    --from-file=/home/chase/k8s-homelab/dashboards/logs.json \
    --dry-run=client -o yaml | kubectl apply -f -
  ```

  The checked-in `dashboards/dashboards-configmap.yaml` is the source for
  deployment. Because the object is large, replace it rather than using a
  normal `kubectl apply` if the existing last-applied annotation exceeds the
  Kubernetes annotation limit.

  **If the dashboards agent also produced a
  `grafana-provisioning-configmap.yaml`** (a provisioning ConfigMap, as
  opposed to a dashboard-content one): that duplicates
  `grafana-provisioning.yaml` in this directory. **Do not apply it.**
  `observability/grafana-provisioning.yaml` is the one `grafana.yaml`'s
  volumes actually reference (`grafana-provisioning-datasources`,
  `grafana-provisioning-dashboards`) -- applying a second, differently-
  named provisioning ConfigMap is harmless on its own (nothing mounts
  it) but applying one with the *same* names as an object with different
  content will silently flip Grafana's provisioning between two
  competing versions depending on which was applied last. Check
  `/home/chase/k8s-homelab/dashboards/` for that file before cutover; if
  it exists, reconcile the two by hand (keep this directory's version --
  it's the one wired into the actual Deployment mounts) rather than
  applying both.

## Apply order

```bash
kubectl apply -f namespace.yaml

# Config first (Deployments below reference these ConfigMaps).
kubectl apply -f loki-config.yaml
kubectl apply -f prometheus-config.yaml
kubectl apply -f grafana-provisioning.yaml   # see ownership note above
kubectl apply -f promtail-config.yaml

# Stateful stores. For EACH of these: stop the corresponding Docker
# container, copy its data dir into the new PVC, chown it (see below),
# THEN apply. Applying first and copying data in after just means the
# pod crash-loops until the copy+chown is done -- not fatal, but do the
# copy+chown before apply to avoid the noise.
kubectl apply -f loki.yaml
kubectl apply -f prometheus.yaml

# Local Discord alerting provisioning is disabled until its real Secret is
# restored; see SECRETS.md before re-enabling the mount and env var.
kubectl apply -f grafana.yaml

# Cluster-facing collectors/exporters. No PVCs, no data migration.
kubectl apply -f kube-state-metrics.yaml
kubectl apply -f promtail.yaml
```

`grafana.yaml` contains the Grafana Deployment/PVC/SA block and a **primary
Service** on port 3002 that will sit `EXTERNAL-IP <pending>` until the
matching old Docker container is stopped -- see "Verification-only
files" below for the actual cutover sequence for those two ports.

## Pantry-bot / Twitch alerting to a Discord DM

### k3s-watcher cloudflared noise boundary

The host-side `k3s-watcher` source is outside this repository at
`/home/chase/docker/observability/monitoring/k3s-watcher/watcher.py`; it is not
part of the k3s manifests and cannot be changed by a repo-only deployment.
The current source already scopes benign QUIC teardown suppression to streams
whose Loki `container` label is exactly `cloudflared`. It skips `context
canceled` and `accept stream listener encountered a failure while serving`,
while broad error patterns still alert for other containers and for real
cloudflared origin failures such as connection refused, dial errors, and
timeouts. The adjacent `test_watcher.py` verifies this container boundary.

The watcher also takes a non-blocking singleton lock, so duplicate watcher
processes exit instead of sending duplicate Discord alerts. If duplicate
notifications recur, inspect the systemd user unit/process list and the lock
path before changing alert patterns. Keep this source mapping in mind when
reviewing a future repo PR: changing `promtail` labels or container names can
silently defeat the scoped suppression.

#### Safe follow-up design for QUIC teardown noise

The suppression should remain a two-stage decision, implemented in the
host-side watcher (not in Promtail):

1. Match the exact `cloudflared` container label and a narrow teardown pattern.
2. Before suppressing, query the connector's `/ready` endpoint through its
   ClusterIP Service (`cloudflared.pantry-bot.svc:2000/ready`). Suppress only
   when the response is successful; otherwise emit the original line so a
   simultaneous connector/origin outage remains visible.

The health query must be bounded (for example, a 2-second timeout), cached for
the current polling cycle, and fail open (an unavailable health check means
alert, not suppression). Do not suppress generic `timeout`, `dial`, DNS, or
origin-connection errors. Because the watcher source and service-account
credentials are host-local/out-of-repo, this repository documents the design
but cannot safely implement or deploy it; the next watcher-source change
should add unit tests for healthy and unhealthy `/ready` responses plus a
fail-open timeout case.

`grafana-provisioning.yaml` carries a retained `grafana-provisioning-alerting`
ConfigMap that can provision when the real webhook Secret and mount are
restored:

- a **Discord contact point** (`discord-pantry-twitch-dm`) whose webhook URL
  is interpolated from the `PANTY_TWITCH_DISCORD_WEBHOOK_URL` env var (never
  committed here) and whose message pings `<@204282471506771971>`;
- a **notification policy** that routes pantry-bot/Twitch alert rules to that
  contact point **in addition to** the existing root receiver
  (`continue: true`), matching rules either in a `pantry-bot`/`twitch`-named
  folder (the `grafana_folder` label) or carrying an `app` label matching
  `pantry-bot|pantry|twitch`.

Facts that shape how you use this:

- **Grafana ships no alert rules today.** The live grafana.db (inspected
  2026-08-12) has zero alert rules and only the stock placeholder contact
  point (`grafana-default-email`). There were no Twitch rules to reproduce,
  so the provisioning file deliberately provisions no rules. Create rules in
  the UI and keep them in a pantry-bot/Twitch folder (or add an
  `app="pantry-bot"` label) and this policy DMs 204282471506771971.
- **The current Pantry Twitch-bot alerts come from elsewhere.** The host
  `k3s-watcher` systemd service
  (`/home/chase/.config/systemd/user/k3s-watcher.service` →
  `/home/chase/docker/observability/monitoring/k3s-watcher/watcher.py`)
  polls Loki and DMs log-error/restart-loop alerts to user
  `929216447723499562`. It is untouched by this change, so the existing
  recipient keeps receiving alerts; Grafana is the *additional* channel to
  `204282471506771971`. Do not expect this provisioning to make Grafana
  duplicate k3s-watcher's messages.
- **Restart required to apply changes.** Alerting provisioning is read once
  at Grafana startup, not watched. After changing either this provisioning
  file or the `grafana-discord-webhooks` Secret, `kubectl -n observability
  rollout restart deploy/grafana`.
- **Policy tree is replaced wholesale.** The `policies` block in
  `alerting.yaml` is the complete tree; any policy you later create in the UI
  is overwritten at the next Grafana restart. Keep editing the provisioning
  file, not the UI.

## Verification-only files -- when to apply, when to delete

Docker Desktop's proxy holds host port **3002** (grafana) until its old
container is stopped, so the real
`LoadBalancer` Services for those two apps cannot bind until then. These
two files exist to make "verify the new instance before you commit to
it" possible in the meantime:

| File | NodePort | URL | Apply | Delete |
|---|---|---|---|---|
| `grafana-verify-nodeport.yaml` | 30002 | `http://192.168.40.208:30002` | Any time after `grafana.yaml` is applied and its pod is Ready. | Immediately after `kubectl -n observability get svc grafana` shows `EXTERNAL-IP 192.168.40.208`. |
| `loki-external-nodeport.yaml` | 31100 | (internal, used by the old Docker promtail) | During Phase 1, once the new Loki is verified. | At Phase 5, once Docker Desktop (and its promtail) is retired. |

Both 30001/30002 are inside k3s's default NodePort range
(30000-32767) -- rev. 1 of this plan proposed an out-of-range port
(25567) that would have been rejected at `kubectl apply` time.

Cutover sequence for Grafana:

```bash
# 1. Verify on the NodePort first.
kubectl apply -f grafana-verify-nodeport.yaml
#    ...browse http://192.168.40.208:30002, confirm datasources,
#       dashboards, and historical data look right...

# 2. Stop the old container.
docker compose -f /home/chase/docker/observability/docker-compose.yml stop grafana

# 3. Re-create the Service so klipper (ServiceLB) retries the bind on
#    the now-free port. A plain re-apply is not reliable here -- klipper
#    does not always re-attempt a bind it already backed off from.
kubectl -n observability delete svc grafana
kubectl apply -f grafana.yaml

# 4. Confirm it's live on the real port.
kubectl -n observability get svc grafana   # EXTERNAL-IP should be 192.168.40.208

# 5. Drop the temporary port.
kubectl delete -f grafana-verify-nodeport.yaml
```

## Manual `chown` commands -- required before each stateful pod starts

All four data directories under `/home/chase/docker/observability/` are
owned `1000:1000` today. Docker Desktop's virtiofs fakes ownership, so
this "worked" there; bare-metal k3s enforces real UID/GID checks and
each app will crash-loop on permission errors without this step. Each
manifest sets `fsGroup` to the matching GID so the PVC is
group-accessible, but **ownership itself is not fixed automatically** --
`fsGroup` only chgrp's the volume root (and, depending on
`fsGroupChangePolicy`, its contents) at mount time; it does not chown
individual files that were copied in as `1000:1000` by a `cp -a`. Run
these by hand, with the corresponding old container stopped and the new
pod scaled to 0, immediately after copying data into each PVC:

```bash
# Loki  (grafana/loki:3.3.2 runs as 10001:10001)
LOKI_PV=$(kubectl -n observability get pvc loki-data -o jsonpath='{.spec.volumeName}')
LOKI_DIR=$(kubectl get pv "$LOKI_PV" -o jsonpath='{.spec.local.path}')
sudo cp -a /home/chase/docker/observability/loki-data/. "$LOKI_DIR"/
sudo chown -R 10001:10001 "$LOKI_DIR"

# Prometheus  (prom/prometheus:v3.1.0 runs as 65534:65534, "nobody")
PROM_PV=$(kubectl -n observability get pvc prometheus-data -o jsonpath='{.spec.volumeName}')
PROM_DIR=$(kubectl get pv "$PROM_PV" -o jsonpath='{.spec.local.path}')
sudo cp -a /home/chase/docker/observability/prometheus-data/. "$PROM_DIR"/
sudo chown -R 65534:65534 "$PROM_DIR"

# Grafana  (grafana/grafana:12.4.2 runs as 472:472)
GRAF_PV=$(kubectl -n observability get pvc grafana-data -o jsonpath='{.spec.volumeName}')
GRAF_DIR=$(kubectl get pv "$GRAF_PV" -o jsonpath='{.spec.local.path}')
sudo cp -a /home/chase/docker/observability/grafana-data/. "$GRAF_DIR"/
sudo chown -R 472:472 "$GRAF_DIR"

```

Notes:
- `local-path-provisioner` only creates the PV (and therefore a
  `kubectl get pv ... -o jsonpath='{.spec.local.path}'`-resolvable path)
  **after** the PVC has been bound by a pod at least once. If the
  `jsonpath` command above returns nothing, apply the manifest once
  first (the pod will crash-loop on the permission error -- that's
  expected), grab the path, do the copy+chown, then let the pod restart.
- Do the copy with the OLD container stopped in every case. Loki's and
  Prometheus' WAL can be corrupted by a hot copy -- this is called out per-app
  in the relevant manifest as well.
- `fsGroupChangePolicy: OnRootMismatch` is set on every stateful pod so
  that, after this one-time chown, subsequent pod restarts don't pay a
  full recursive `chgrp` walk across 20Gi of Loki chunks / Prometheus
  blocks on a 5900rpm HDD.

## Prometheus scrape targets that need the node IP

`prometheus-config.yaml` points `node`, `docker-desktop-exporter`, and
`minecraft-exporter` at `192.168.40.208:{9100,9201,9202}` instead of the
Compose-era `host.docker.internal`, which does not resolve inside a k3s
pod. `docker-desktop-exporter` (:9201) is marked in that file as
**to-be-removed at Phase 5** -- delete that scrape job in the same
change that retires Docker Desktop, or it alerts "target down" forever.

## Log severity parsing (promtail)

`promtail-config.yaml` adds `pipeline_stages` per job to extract a
`level` label (`debug`/`info`/`warn`/`error`), with a regex/multiline
strategy tailored to each app's log format (Log4j-bracketed for
jmusicbot/Minecraft, keyword-heuristic for pantry-bot's plain
Node console output, logfmt/JSON/klog for k8s system components). See
the header comment in that file for the exact detection rules and why
unmatched lines default to `info` rather than being mislabeled. Verify
with a Grafana Explore query against Loki:

```
{namespace="observability"} | level="error"
```

should return only genuinely error-level lines, once at least one app is
producing some.
