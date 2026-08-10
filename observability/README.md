# observability namespace

Kubernetes manifests for the `observability` namespace of the single-node
k3s home-lab migration (loki, promtail, prometheus, grafana,
kube-state-metrics, uptime-kuma). Ported from
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
| `grafana-provisioning.yaml` | ConfigMaps: `grafana-provisioning-datasources`, `grafana-provisioning-dashboards`, and a placeholder `grafana-dashboards`. **See "Grafana dashboard ConfigMap" below before applying.** |
| `grafana-verify-nodeport.yaml` | **Verification only.** NodePort 30002. |
| `uptime-kuma.yaml` | uptime-kuma SA, PVC (2Gi), Deployment, primary `LoadBalancer` Service (3001). |
| `uptime-kuma-verify-nodeport.yaml` | **Verification only.** NodePort 30001. |
| `promtail.yaml` | promtail SA, ClusterRole/Binding (API access for pod discovery), DaemonSet, ClusterIP Service (9080, metrics only). |
| `promtail-config.yaml` | ConfigMap: promtail's config, incl. severity-extraction `pipeline_stages`. |
| `kube-state-metrics.yaml` | kube-state-metrics SA, ClusterRole/Binding, Deployment, headless Service (8080/8081). |

Every stateful app (loki, prometheus, grafana, uptime-kuma) is a
`Deployment` with `strategy: Recreate` and a `ReadWriteOnce` PVC -- never
a StatefulSet, never `RollingUpdate`. promtail is a DaemonSet (no PVC).
kube-state-metrics is a stateless `Deployment` with ordinary
`RollingUpdate` (no PVC, nothing to lose during a rollout).

## Grafana dashboard ConfigMap -- ownership boundary

Two different agents touch Grafana's ConfigMaps and it's worth being
explicit about the split, since it's easy to end up with two conflicting
`provisioning/datasources` or `provisioning/dashboards` ConfigMaps:

- **This directory (`observability/`) owns the *provisioning*
  ConfigMaps** -- `grafana-provisioning-datasources` and
  `grafana-provisioning-dashboards`, both defined in
  `grafana-provisioning.yaml`. These are mounted by `grafana.yaml` at
  `/etc/grafana/provisioning/{datasources,dashboards}`. This is the
  wiring the migration plan calls out explicitly (rev. 2, "Grafana
  manifest dropped the `dashboards` mount that the provisioning config
  references") -- getting it wrong yields a Grafana with zero
  dashboards and no obvious error, so treat these two ConfigMaps as
  authoritative and don't duplicate them elsewhere.

- **A separate agent, writing into `/home/chase/k8s-homelab/dashboards/`,
  owns the dashboard *content*.** As of this writing that directory
  contains the raw dashboard JSON (`cluster-overview.json`,
  `pods-and-workloads.json`, `logs.json`) but no ConfigMap YAML yet.
  `grafana.yaml` already mounts a ConfigMap named **`grafana-dashboards`**
  at `/etc/grafana/dashboards` (the placeholder version in
  `grafana-provisioning.yaml` is there just so `kubectl apply -f
  observability/` produces a working, if dashboard-less, Grafana before
  that lands). **The dashboards agent's generated ConfigMap must be named
  `grafana-dashboards`** -- that's the contract both agents were given.
  When it lands, replace the placeholder:

  ```bash
  kubectl -n observability create configmap grafana-dashboards \
    --from-file=/home/chase/k8s-homelab/dashboards/cluster-overview.json \
    --from-file=/home/chase/k8s-homelab/dashboards/pods-and-workloads.json \
    --from-file=/home/chase/k8s-homelab/dashboards/logs.json \
    --dry-run=client -o yaml | kubectl apply -f -
  ```

  If the dashboards agent instead ships its own
  `dashboards-configmap.yaml` that already defines a ConfigMap named
  `grafana-dashboards`, apply that file directly and skip the command
  above -- just don't apply both for the same ConfigMap name.

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
kubectl apply -f grafana.yaml
kubectl apply -f uptime-kuma.yaml

# Cluster-facing collectors/exporters. No PVCs, no data migration.
kubectl apply -f kube-state-metrics.yaml
kubectl apply -f promtail.yaml
```

`grafana.yaml` and `uptime-kuma.yaml` each contain a Deployment/PVC/SA
block that is safe to apply any time, plus a **primary Service** on the
real port (3002 / 3001) that will sit `EXTERNAL-IP <pending>` until the
matching old Docker container is stopped -- see "Verification-only
files" below for the actual cutover sequence for those two ports.

## Verification-only files -- when to apply, when to delete

Docker Desktop's proxy holds host ports **3001** (uptime-kuma) and
**3002** (grafana) until their old containers are stopped, so the real
`LoadBalancer` Services for those two apps cannot bind until then. These
two files exist to make "verify the new instance before you commit to
it" possible in the meantime:

| File | NodePort | URL | Apply | Delete |
|---|---|---|---|---|
| `grafana-verify-nodeport.yaml` | 30002 | `http://192.168.40.208:30002` | Any time after `grafana.yaml` is applied and its pod is Ready. | Immediately after `kubectl -n observability get svc grafana` shows `EXTERNAL-IP 192.168.40.208`. |
| `uptime-kuma-verify-nodeport.yaml` | 30001 | `http://192.168.40.208:30001` | Any time after `uptime-kuma.yaml` is applied and its pod is Ready. | Immediately after `kubectl -n observability get svc uptime-kuma` shows `EXTERNAL-IP 192.168.40.208`. |
| `loki-external-nodeport.yaml` | 31100 | (internal, used by the old Docker promtail) | During Phase 1, once the new Loki is verified. | At Phase 5, once Docker Desktop (and its promtail) is retired. |

Both 30001/30002 are inside k3s's default NodePort range
(30000-32767) -- rev. 1 of this plan proposed an out-of-range port
(25567) that would have been rejected at `kubectl apply` time.

Cutover sequence for Grafana (uptime-kuma is identical, s/grafana/uptime-kuma/, s/3002/3001/):

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

# Grafana  (grafana/grafana:11.4.0 runs as 472:472)
GRAF_PV=$(kubectl -n observability get pvc grafana-data -o jsonpath='{.spec.volumeName}')
GRAF_DIR=$(kubectl get pv "$GRAF_PV" -o jsonpath='{.spec.local.path}')
sudo cp -a /home/chase/docker/observability/grafana-data/. "$GRAF_DIR"/
sudo chown -R 472:472 "$GRAF_DIR"

# uptime-kuma  (louislam/uptime-kuma:2 runs as root, 0:0)
KUMA_PV=$(kubectl -n observability get pvc uptime-kuma-data -o jsonpath='{.spec.volumeName}')
KUMA_DIR=$(kubectl get pv "$KUMA_PV" -o jsonpath='{.spec.local.path}')
sudo cp -a /home/chase/docker/observability/uptime-kuma-data/. "$KUMA_DIR"/
sudo chown -R 0:0 "$KUMA_DIR"
```

Notes:
- `local-path-provisioner` only creates the PV (and therefore a
  `kubectl get pv ... -o jsonpath='{.spec.local.path}'`-resolvable path)
  **after** the PVC has been bound by a pod at least once. If the
  `jsonpath` command above returns nothing, apply the manifest once
  first (the pod will crash-loop on the permission error -- that's
  expected), grab the path, do the copy+chown, then let the pod restart.
- Do the copy with the OLD container stopped in every case. Loki's and
  Prometheus' WAL and uptime-kuma's embedded MariaDB datadir are all
  corrupted by a hot copy -- this is called out per-app in the relevant
  manifest as well.
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
