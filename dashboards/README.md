# Grafana dashboards for the k3s home-lab

Twelve dashboards, built to replace the four that ran under Docker Desktop
(`host-overview`, `container-resources`, `minecraft`, `logs-overview` — see
`/home/chase/docker/observability/monitoring/grafana/dashboards/` for the
originals, kept read-only for reference) with equivalents that understand
Kubernetes objects, not just containers.

## Provisioning: which file is the source of truth

**Two agents worked on this in parallel and both touched Grafana
provisioning. This section is the reconciliation.**

- **`/home/chase/k8s-homelab/observability/grafana-provisioning.yaml`
  is authoritative** for `datasources.yaml` and `dashboards.yaml` (the
  two provider configs Grafana reads from
  `/etc/grafana/provisioning/{datasources,dashboards}/`), and it is
  already wired up: `observability/grafana.yaml`'s Deployment mounts
  `grafana-provisioning-datasources` and `grafana-provisioning-dashboards`
  from that file at the right paths. **This directory (`dashboards/`)
  deliberately does not contain a second, competing
  `grafana-provisioning-configmap.yaml`.** Writing one here would have
  produced exactly the "two conflicting sources" problem this note
  exists to prevent — Grafana would either use whichever ConfigMap got
  applied last, or (worse) someone edits one copy in six months and not
  the other.
- **`dashboards/dashboards-configmap.yaml` (this directory) is
  authoritative** for the dashboard *content* — the `grafana-dashboards`
  ConfigMap, mounted at `/etc/grafana/dashboards`, which is the path
  `grafana-provisioning.yaml`'s `dashboards.yaml` provider points at.
  `grafana-provisioning.yaml` ships a `.placeholder`-only stub of this
  same ConfigMap name so that `kubectl apply -f .` over the whole
  `observability/` directory produces a working (if dashboard-less)
  Grafana even before this directory's content lands. **Apply
  `dashboards/dashboards-configmap.yaml` after (or instead of) that
  placeholder** — same name, same namespace, it replaces it wholesale.

Net result, one apply order:

```sh
kubectl apply -f k8s-homelab/observability/namespace.yaml
kubectl apply -f k8s-homelab/observability/grafana-provisioning.yaml   # datasources + dashboards providers + placeholder grafana-dashboards
kubectl apply -f k8s-homelab/dashboards/dashboards-configmap.yaml      # REPLACES the placeholder with the real 9 dashboards
kubectl apply -f k8s-homelab/observability/grafana.yaml                # Deployment/Service/PVC/SA
```

If a dashboard JSON in this directory changes, regenerate the ConfigMap
rather than hand-editing the YAML (the embedded JSON will drift from the
`.json` files otherwise, and the `.json` files are what you actually
edit):

```sh
cd /home/chase/k8s-homelab/dashboards
kubectl create configmap grafana-dashboards -n observability \
  --from-file=apps.json=apps.json \
  --from-file=cluster-overview.json=cluster-overview.json \
  --from-file=host-pc.json=host-pc.json \
  --from-file=jmusicbot.json=jmusicbot.json \
  --from-file=logs.json=logs.json \
  --from-file=overview.json=overview.json \
  --from-file=pantry-bot.json=pantry-bot.json \
  --from-file=pantry-bot-postgres.json=pantry-bot-postgres.json \
  --from-file=pantry-bot-usage.json=pantry-bot-usage.json \
  --from-file=pods-and-workloads.json=pods-and-workloads.json \
  --dry-run=client -o yaml
```
then paste the `data:` block back into `dashboards-configmap.yaml` under
its existing `metadata:` (labels included), or just `kubectl apply -f -`
the command's output directly against a live cluster — same object,
same name, same namespace, it overwrites in place.

**Size**: all twelve dashboards remain comfortably below the 1 MiB ConfigMap
etcd/API object-size ceiling for a ConfigMap is 1 MiB, so one ConfigMap
comfortably hold them in one object — no split needed. If more dashboards get
added later and this approaches ~900 KiB, split by file and add one
extra volume + volumeMount pair to `grafana.yaml`'s Deployment rather
than truncating silently.

**Datasource UIDs**: every dashboard here uses `${DS_PROMETHEUS}` /
`${DS_LOKI}` `type: datasource` template variables instead of hardcoded
UIDs. `grafana-provisioning.yaml` defines exactly one datasource of
each type (`uid: Prometheus`, `uid: Loki`), so both variables resolve
automatically on load with no manual selection needed — this also means
the dashboards import cleanly into any Grafana instance regardless of
what UID that instance happens to assign, as long as there's one
Prometheus and one Loki source configured.

## The dashboards

### `pantry-bot-usage.json` — Pantry Bot — Usage

This standalone dashboard reads the bot's Prometheus `/metrics` endpoint. It
shows successful and rejected engagement actions, damage and outcomes, action
and source mix, bot message volume, and extension route volume. Counters are
process-local and reset on a bot restart, so panels use `increase()`/`rate()`;
participant history and first-time participation remain durable in SQLite.

Open it at `/d/homelab-pantry-bot-usage/pantry-bot-usage` after Grafana loads the
provisioned dashboard.

### `pantry-bot-postgres.json` — Pantry Bot — Usage (Postgres)

Obs-only workaround for the empty Prometheus usage dashboard (no code
rebuild): reads the bot's PostgreSQL tables through the home standby
replica instead of `/metrics` scraping. Covers ingest rate, queue depth,
dead-letter outbox, community game actions, boss damage/outcomes, daily
bot messages, outbound deliveries by target, and distinct participants.
Needs the `PantryPostgres` datasource (`observability/grafana-provisioning.yaml`,
password from the imperative `grafana-postgres-pantry` Secret — see
`observability/grafana.yaml`). Extension routes and per-command panels
have no backing table and stay pending the per-role `/metrics` code fix.

Open it at `/d/homelab-pantry-bot-postgres/pantry-bot-usage-postgres` after Grafana loads the
provisioned dashboard.

### `host-pc.json` — Main PC — Bare-Metal Health

This is the focused physical-host dashboard for bare-metal health.
It intentionally excludes pod, deployment, kube-system, and Prometheus-target
panels so it answers a different question from `cluster-overview.json`: is the
PC itself being overworked by containers or other processes? It covers whole-host
CPU, memory pressure, root-disk capacity, uptime, load versus CPU cores, memory
breakdown, disk utilisation/throughput, physical NIC traffic, and temperatures.
The host selector uses the native `node_exporter` `node_uname_info` series.

Open it at `/d/homelab-host-pc/main-pc-bare-metal-health` (or use the link on
the Overview dashboard). `cluster-overview.json` remains the mixed host + k3s
control-plane dashboard for workload-aware triage.

### `cluster-overview.json` — Host Health / Cluster Overview — Node & k3s Control Plane

**This is the host-health dashboard.** If you're looking for "where is the
host / host-health dashboard in Grafana", this is it — the k8s successor to
the old Docker `host-overview` dashboard (from the four retired above). It
ships a stable `uid: homelab-cluster-overview`, tags `homelab, k3s, node,
host`, and the title/tags have carried the `Host Health` name since commit
`5b2f7b2`. Because Grafana's file provisioning keys on the
JSON `uid` — never the title — renaming it did not change URLs/bookmarks and
cannot duplicate the dashboard.

Node-level health for the bare-metal host (`MinecraftMachine`), sourced
from the **native `node_exporter` binary at `192.168.40.208:9100`** —
there is intentionally no node-exporter DaemonSet (it would collide with
the systemd unit already running and never go Ready; see the migration
plan). "Node" throughout this dashboard means the real machine, not a
container's view of it.

Panels: CPU/memory/disk gauges, a `node` variable (in case a second node
is ever added) and a `disk` variable for the I/O panels, k3s Node-Ready
status, load average plotted against a dashed core-count reference line,
CPU-by-mode (watch `iowait` for HDD contention), a memory breakdown with
dashed reference lines for **total physical capacity (~15 GiB)** and the
kubelet's `system-reserved=3Gi` floor, scheduled pod memory requests vs
allocatable, swap activity (host-only — **pods cannot swap**, kubelet
sets `memory.swap.max=0` on cgroup v2, a container at its limit is
OOM-killed with no GC chance, not swapped), and a full **disk I/O
latency / utilisation / queue-depth / IOPS** section — root (`/`) is a
5900rpm `ST4000DM000`, called out explicitly per the migration plan
since Prometheus compaction, Loki chunk flush and Minecraft world saves
all contend for the same spindle. Also: kube-system pod table/restarts
(k3s's apiserver/scheduler/controller-manager are **not pods** on k3s —
they run inside the single k3s process, so there's no `kube_pod_*`
series for them; use the Node-Ready stat for that), scrape-target-down
count, node-pressure-condition count, and hwmon temperatures.

**Monitoring scope — this dashboard, k3s-watcher, and UptimeRobot.**
Prometheus is pinned to **7d / 15 GB retention**
(`observability/prometheus.yaml`), deliberately — this host's `/` is a
5900rpm HDD and huge TSDB blocks are hostile to it (HDD-tuning note in
`ARCHITECTURE.md`). The chosen split is:

- **Current host health / current host stats live here in Grafana.** This
  dashboard is the main-PC view for CPU, memory, disk, temperatures, network,
  load, and k3s control-plane health.
- **k3s-watcher owns workload and node alerting.** It sends Discord/Operations
  alerts for node readiness, connector readiness, restart loops, log errors,
  and the configured functional health URLs.
- **UptimeRobot owns external reachability.** Keep Home and Oracle checks
  pointed at node-specific IP/ports; public HTTP checks may use existing
  Cloudflare hostnames, but must not be used to infer which tunnel replica is
  alive. Prometheus/Grafana retain resource history.

### `pods-and-workloads.json` — Pods & Workloads — Usage vs Limits

Per-namespace / per-pod view across every namespace exposed by
`kube-state-metrics`, including `observability`, `jmusicbot`,
`pantry-bot`, and `minecraft`; the namespace selector is populated from
live `kube-state-metrics` labels and includes an explicit all-namespaces
option. The panel the spec called out specifically —
**usage plotted against the pod's configured limit**, not usage alone —
is panel 9 (memory, solid = working set from cAdvisor, dashed = limit
from kube-state-metrics) and panel 10 (memory used as % of limit,
bar-gauge, red above 85%), plus the CPU equivalents (panels 11–12,
including CFS throttling %). For Minecraft specifically this is where
you see the 8Gi limit line against actual working-set memory.

Also: pod phase/restarts/readiness (table + state-timeline), OOMKill
events (`container_oom_events_total` from kubelet's cAdvisor endpoint —
**flagged as unreliable on some runtimes**, cross-checked against
`kube_pod_container_status_last_terminated_reason`, which is sourced
independently and is the more trustworthy "was this an OOMKill" signal),
Deployment desired-vs-available replicas, pod network I/O, and container
filesystem read/write rate.

**One panel carries a real TODO, not a guess**: "PersistentVolumeClaim
Usage" uses `kubelet_volume_stats_*`, which is exposed by the kubelet's
own `/metrics` endpoint, **not** `/metrics/cadvisor`. The migration plan
only commits to scraping `/metrics/cadvisor`; if a second scrape job for
the kubelet's main metrics endpoint (`:10250/metrics`, bearer token) was
never added to `prometheus.yml`, this panel will be empty — that's
called out in the panel description and in a `noValue` message, not
silently guessed at.

### `logs.json` — Logs — Search & Severity — **the key new deliverable**

Everything the plan's log-search requirement asked for:

- **`level` variable** (custom, multi-select `debug`/`info`/`warn`/`error`,
  `includeAll` with `allValue: ".*"`) wired directly into every LogQL
  stream selector as `level=~"$level"`. This is the label promtail's
  `pipeline_stages` extracts at ingest — filtering happens at the Loki
  query layer, not client-side text matching, so it actually scales and
  actually works for Java/Log4j (jmusicbot, minecraft) and Node.js
  (pantry-bot) logs alike, whatever their native format.
- **`namespace` variable** doubles as the app selector (each app has its
  own namespace) and a **`pod` variable** scoped under it
  (`label_values({namespace=~"$namespace"}, pod)`), for drilling into one
  pod when a Deployment briefly has two pods during a `Recreate` rollout.
- **`search` free-text variable**, applied as a case-insensitive LogQL
  line filter (`|~ "(?i)$search"`), RE2 syntax.
- **Log Volume by Severity** (panel 6): stacked bars, one series per
  level, colour-coded (error=red, warn=amber, info=blue, debug=grey,
  unclassified=dark grey) — an error spike is a red block that stands
  out on sight, not something you have to read log lines to notice.
- **ERROR lines stat** (panel 2) and a **permanently error-scoped log
  panel** (panel 10) that both deliberately ignore the `$level` variable
  — they always show errors regardless of what the severity filter is
  set to, so "how many errors right now" never depends on remembering to
  reset a dropdown.
- **"Unclassified lines" stat** (panel 4): counts `level=""` — lines
  promtail could not classify at all. These are invisible to the
  severity filter by construction (an empty label can't match a
  non-empty regex the user typed), so a large number here is the signal
  that some app's promtail pipeline stage needs a regex/JSON stage
  written for it, not that the dashboard is broken.

**How the severity filter mechanically works**: promtail's
`pipeline_stages` (in
`/home/chase/k8s-homelab/observability/promtail-config.yaml` /
`promtail.yaml`) run a regex or JSON stage per job that extracts a
`level` value from each line and promotes it to a Loki **label** (not
just a parsed field) via a `labels:` stage. Because it's a label, every
LogQL query in this dashboard can select on it directly in the stream
selector — `{namespace=~"$namespace", pod=~"$pod", level=~"$level"}` —
which Loki resolves at the index/chunk level before it even starts
reading log bodies. If that promtail stage is ever removed or breaks for
one app, that app's logs don't disappear — they just stop carrying a
`level` label and fall into the "unclassified" bucket above, which is
exactly what panel 4 exists to catch.

### App dashboards — `apps.json`, `jmusicbot.json`, and `pantry-bot.json`

The Overview dashboard links to the workload dashboards, with Pantry Bot health and usage split into separate views.

App-specific panels, and deliberately not padded with invented metrics.

- **Minecraft** has a real exporter
  (`k8s-homelab/scripts/minecraft_exporter.py`), RCON-based, reachable
  in-cluster via the `minecraft-rcon` ClusterIP Service post-migration:
  `minecraft_tps_1m/5m/15m`, `minecraft_players_online/max`,
  `minecraft_server_up`, `minecraft_world_size_bytes` (polled every 10
  minutes, not continuously — walking thousands of region files on every
  15s tick would itself add HDD load). **Memory is `container_memory_working_set_bytes`
  from cAdvisor, not the old `minecraft_process_memory_bytes`** — that
  gauge came from `psutil` reading the java process's RSS from outside
  its container's PID namespace, which doesn't work once containerized,
  so it was dropped from the exporter (see the script's own header
  comment) and there is no `minecraft_process_cpu_percent` either.
  Memory is plotted against both the 8Gi container limit and a reference
  line at the 6Gi `-Xmx` heap ceiling, so JVM-overhead-above-heap is
  visually distinguishable from real growth. No livenessProbe exists on
  purpose (a SIGKILL mid-tick risks world corruption), so a restart
  count here means an actual OOMKill or process exit, not a liveness
  probe being trigger-happy.
- **jmusicbot** has **no application metrics and never did** (no
  exporter on Docker either) — panels are pod status, restarts, and
  memory-vs-limit (1Gi bot / 128Mi release-notifier) sourced entirely
  from kube-state-metrics + cAdvisor, plus a raw log panel with a note
  pointing at the itag-18 decode-failure string this custom build exists
  to work around.
- **pantry-bot** similarly has no `/health` endpoint — `GET /` returns a
  bare 404 (verified through the live Cloudflare tunnel), which is why
  the deployment uses an `exec` probe reproducing the old Compose
  healthcheck rather than `httpGet` (an `httpGet` probe would treat that
  same 404 as failure and crash-loop a healthy pod — see the extensive
  comment in the retired `pantry-bot/40-deployment.yaml.retired`). So "HTTP health" here is
  honestly just `kube_pod_status_ready`, i.e. whether the exec probe's
  TCP connect + any-response check is passing — **not** request latency,
  status codes, or whether Twitch auth/SQLite/the tunnel actually work.
  The panel description says exactly this and gives the concrete
  follow-up (add a real `/healthz` route, switch to `httpGet`, then
  optionally instrument with `prom-client`) rather than inventing a
  metric name that doesn't exist. Cloudflared is separately dashboarded from
  Kubernetes readiness/running/restart/resource metrics and its Loki logs;
  its native `:2000` endpoint is used by probes but is not scraped by
  Prometheus.

## Metric-existence notes (why each metric was chosen)

- `node_*` — native `node_exporter` on `192.168.40.208:9100`.
- `container_memory_working_set_bytes`, `container_cpu_usage_seconds_total`,
  `container_cpu_cfs_throttled_periods_total` / `_periods_total`,
  `container_network_{receive,transmit}_bytes_total`,
  `container_fs_{reads,writes}_bytes_total`, `container_oom_events_total`
  — kubelet's built-in `/metrics/cadvisor`. `container_oom_events_total`
  was confirmed to exist in cAdvisor as of the version embedded in
  current kubelets, with a known caveat (linked in the panel
  description) that it has been reported stuck at zero in some
  container-runtime combinations — that's why the pods dashboard
  cross-checks it against `kube_pod_container_status_last_terminated_reason`
  rather than trusting it alone.
- `kube_pod_status_phase`, `kube_pod_status_ready`,
  `kube_pod_container_status_restarts_total`,
  `kube_pod_container_status_last_terminated_reason`,
  `kube_pod_container_resource_limits` / `_requests`,
  `kube_node_status_condition` / `_allocatable`,
  `kube_deployment_spec_replicas` / `status_replicas_available` /
  `_unavailable`, `kube_pod_container_status_running` — kube-state-metrics.
  `kube_pod_container_resource_limits`/`_requests` carry generic
  `resource`/`unit` labels (`resource="memory"`, `resource="cpu"`) as of
  kube-state-metrics v2 — every query in these dashboards uses that
  label form, not the pre-v2 per-resource metric names.
- `kubelet_volume_stats_*` — **flagged, not assumed**. See the
  PersistentVolumeClaim Usage panel note above.
- `minecraft_*` — the bespoke exporter, unchanged names except memory
  (see Apps section above).

## What's intentionally not here

Two panels from the old Docker dashboards don't have a k8s equivalent
and were dropped rather than faked:
- `docker_desktop_vm_memory_bytes` (Host Overview's memory pie chart) —
  Docker Desktop is retired at the end of this migration; there's no VM
  reservation left to visualize.
- Per-container network via a bespoke `network-exporter` — cAdvisor's own
  `container_network_*` series are used instead now that cadvisor is the
  kubelet's built-in one rather than the old stack's privileged
  standalone container (the old dashboard avoided cadvisor for network
  specifically because it wasn't reliable in that setup; the k8s
  cAdvisor path doesn't have that same caveat, so it doesn't need a
  parallel exporter).
