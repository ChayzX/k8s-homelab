# MinecraftMachine k3s Home-Lab — Architecture Reference

**Read this first.** This document is the single source of truth for how this cluster is built: every namespace, every workload, every port, every Service DNS name, the RBAC model, and the storage layout. It's written so a human or an AI agent can understand the whole system without reading every manifest — but the manifests are still the actual source of truth for exact syntax; this is the map, not the territory.

**Status flag — read before trusting anything below as "live":** cutover is **complete** for every namespace this repo's git history tracks. The cluster and deployment facts were revalidated during the September 2026 availability/recovery review; older migration timestamps below are historical context, not the current verification timestamp:
- **Applied and live**: `_bootstrap`, `observability` (all six workloads `1/1 Running`), `jmusicbot` (fully migrated, old Docker containers stopped), `pantry-bot` (applied, healthy, real production data migrated, Cloudflare Tunnel fully cut over, deploy pipeline migrated to GitHub Actions — see below), `minecraft` (fully migrated — `msh.service` is `inactive`, the `minecraft` LoadBalancer Service is bound at `192.168.40.208:25565`; see its section below), `opsbot` (live, GHCR-built, deployed via GitHub Actions — see its own section below), `ci-tunnel` (dedicated Cloudflare Tunnel connector for CI kubectl access, see `k8s-homelab-oiv.8`).
- **Removed**: `keel` — decommissioned 2026-08-13, retired in favor of the GitHub Actions deploy pipeline (see below and `k8s-homelab-oiv`). `scotty` (Beads' web UI) — retired with the beads migration 2026-08-15 (see AGENTS.md; issue tracking now lives in GitHub Issues + Projects v2).
- **Namespace created, nothing else applied**: none remaining.
- **Not started at all**: none remaining in this repo's tracked migration scope.
- **Live in the cluster but NOT part of this repo's git-tracked migration**: none remaining — the last holdout, `scotty` (Beads' web UI), was retired with the beads migration 2026-08-15.

**Docker-side observability stack, retired 2026-08-12** (separate from the `observability` namespace above — do not confuse the two): a standalone Docker Compose stack at `/home/chase/docker/observability/` is stopped. Its local alerting was replaced by the k3s-native watcher plus the independent GCP monitor at `/usr/local/lib/homelab-monitor/monitor.py`. **What is NOT retired, deliberately**: Docker Desktop itself and native `docker-ce`; they remain available for manual/local image builds where needed. JMusicBot's scheduled local updater was retired 2026-09-09 in favor of the published GitHub Actions workflow. Historical Beads identifiers in this document are archival references only; GitHub Issues are current tracking.

**CI/CD migrated to GitHub Actions** (`k8s-homelab-oiv`, cut over 2026-08-13): Keel (poll-based auto-deploy, see the retired `keel` namespace section below) is fully decommissioned — a real incident (`k8s-homelab-9nb`: its separate registry credential silently expired, deploys stopped for a day with zero alerting) exposed the risk of an unattended background updater with its own failure-prone credential path. Replaced by GitHub Actions: deploy workflows build+push to GHCR, then apply/restart/verify through a dedicated Cloudflare Tunnel + Access route (`ci-tunnel/` namespace, see `ci-tunnel/MANUAL-SETUP.md`) using a narrowly-scoped `ci-deploy` ServiceAccount. Production workflows now serialize deployments with GitHub Actions concurrency groups. opsbot was also refactored off local-containerd-sideload onto the same GHCR pattern pantry-bot already used. Self-hosted GitHub Actions runners were tried first and abandoned (rootless-Docker and ephemeral-registration issues); fully GitHub-hosted was simpler once the tunnel existed. Historical Beads identifiers in this document are archival references only; GitHub Issues are the current tracker.

**Pantry-bot** (applied 2026-08-11 ~03:58 UTC): all 6 manifests applied. Two live issues were caught and fixed during cutover, not by pre-apply review:
1. *Split-tunnel traffic*: this Cloudflare Tunnel is dashboard-managed (`TUNNEL_TOKEN` only, no local ingress config) and turned out to serve **four** hostnames on one tunnel, not just pantry-bot's two — `oauth.greeniespantry.uk` + `overlay.greeniespantry.uk` (pantry-bot) and `status.greeniespantry.uk` + `grafana.greeniespantry.uk` (uptime-kuma/grafana), previously routed via `host.docker.internal:3001`/`:3002` from the Docker Desktop side. Any connector of a dashboard-managed tunnel serves *all* its hostnames regardless of which one Cloudflare's edge happens to route a given request to — so bringing up a k8s `cloudflared` connector immediately put it in the same pool as the old Docker one, and whichever connector a request landed on had to resolve the origin from *its own* network namespace. The two origin styles are mutually exclusive: `pantry-bot.pantry-bot.svc:*` only resolves from inside the k3s pod network, `host.docker.internal:*` only resolves from the Docker Desktop side. Fix: `status`/`grafana` origins changed to the stable LoadBalancer IP (`192.168.40.208:3001` uptime-kuma, `:3002` grafana — confirmed reachable from both runtimes), then the old Docker `pantry-bot-cloudflared-1` connector was stopped once the k8s connector was confirmed serving all four hostnames correctly. All four verified externally post-cutover (oauth/overlay `404`-on-root — expected, no root route defined, means reachable; status/grafana `302`).
2. *Empty PVC*: applying the manifests gives pantry-bot a fresh, empty SQLite DB — the real data lives in a separate Docker named volume (`pantry-bot_pantry-data`) that manifest application does not touch. Migrated via a **zero-downtime** consistent snapshot: `better-sqlite3`'s `.backup()` API run inside the still-live Docker container (no interruption to production), copied out through a scratch dir (not the repo, to avoid ever committing production data), `kubectl cp`'d into the new pod's PVC, stale empty `-wal`/`-shm` files cleared, pod restarted. Verified via absence of the "No stored broadcaster/bot credentials" bootstrap message post-restart — real OAuth state loaded correctly.

**Correction, found live, not by pre-apply review**: old Docker `pantry-bot-bot-1` and `pantry-bot-watchtower-1` containers were left running post-cutover on the assumption that "no longer receive any traffic" made them a safe, inert fallback during the soak period — that assumption is **wrong** and the user has been asked to stop them. Unlike pantry-bot's own HTTP-only oauth/overlay servers, which only ever respond to *inbound* requests the tunnel is now routing elsewhere, pantry-bot is fundamentally a **Twitch bot**: it opens its own *outbound* connection to Twitch's IRC/EventSub endpoints independent of any inbound tunnel routing. Stopping the tunnel's traffic to the old container does nothing to stop that outbound connection — so the old and new instances were both live and both connected to Twitch simultaneously, a real duplicate-bot bug, not a safe fallback. These two containers should be **stopped**, not left running long-term. The standalone Docker `promtail` container (mounts `docker.sock` + `/home/chase/minecraft/logs`) is still required for as long as these two plus the Minecraft Docker server exist — it's the only thing shipping their logs to Loki, since k8s promtail only tails pod logs. **Was silently broken for the whole soak period until just now**: its client config still pointed at `http://loki:3100`, a sibling Docker container stopped at the observability cutover ~11h before this was caught — meaning none of these logs actually reached Loki since then. Fixed by repointing to k3s Loki's ClusterIP (`10.43.54.179:3100`, host-config file outside this repo at `/home/chase/docker/observability/monitoring/promtail/promtail-config.yaml`) and restarting; verified via `promtail_sent_bytes_total` actually incrementing with zero drops. ClusterIP works here specifically because Docker and k3s share the same host/kernel — this would NOT work from inside a k3s pod or a Docker Desktop VM (see the pantry-bot Cloudflare Tunnel incident above for that exact failure mode). **This container is stopped as of 2026-08-12**, along with the rest of the old Docker-side observability Compose stack it belongs to (see the top status flag) — safe now that Minecraft's `msh.service` is also inactive and nothing writes to `/home/chase/minecraft/logs` anymore.

Cross-check `kubectl get pods -A` / `kubectl get ns` against this document before assuming anything below is deployed — this section is accurate as of the timestamp above, not guaranteed current after it.

**Completeness flag**: sections marked `[PENDING]` are not yet written. Check the section itself for what's missing.

---

## 1. Host

| | |
|---|---|
| Hostname | `MinecraftMachine` |
| OS | Ubuntu 24.04.4 LTS, kernel 7.0.0-28-generic |
| CPU | AMD Ryzen 7 3700X, 8c/16t |
| RAM | 15GiB total |
| Disk (`/`, k3s storage) | `/dev/sda2`, ext4, **5900rpm HDD** (ST4000DM000) — several manifests below tune around this explicitly |
| NVMe (`/dev/nvme0n1p4`) | Mounted at `/mnt/nvme` as ext4 with approximately 188 GiB available according to the 2026-09-08 independent review. It is already used for host data; any service migration must be reversible and must not repartition or erase it. |
| LAN IP | `192.168.40.208`, interface `wlo1` (WiFi), DHCP reservation in place for the host's WiFi MAC |
| GPU | NVIDIA RTX 2060 — used only by the desktop session (Xorg, gnome-remote-desktop's NVENC encoder). Not used by, or available to, anything in this cluster. |
| k3s version | v1.36.3+k3s1, containerd 2.3.2 |
| k3s install flags | `--write-kubeconfig-mode 644 --kubelet-arg=system-reserved=cpu=1000m,memory=3Gi --kubelet-arg=eviction-hard=memory.available<500Mi` — the `system-reserved` flag is why `kubectl get node` shows allocatable memory (~12.0Gi) well below capacity (~15.5Gi): that gap is deliberately held back for the desktop session, which the scheduler would otherwise be blind to. |
| Default StorageClass | `local-path` (k3s-bundled `local-path-provisioner`) — PVCs become plain directories under `/var/lib/rancher/k3s/storage/` on the HDD above |
| Also installed, bundled by k3s | Traefik + its ServiceLB DaemonSet (binds 80/443, unused by anything in this design so far — harmless, costs a little RAM) |
| Native `docker-ce` / `containerd` + Docker Desktop | **Both deliberately left running** alongside k3s (separate containerd instances/sockets from k3s's own, no conflict). They remain available for manual/local builds, especially Minecraft. JMusicBot's daily local updater is retired; its production image now comes from GHCR/GitHub Actions. |

## 2. Namespaces

| Namespace | Created by | Owns |
|---|---|---|
| `jmusicbot` | `jmusicbot/00-namespace.yaml` | Discord music bot + release notifier |
| `pantry-bot` | `_bootstrap/00-namespaces.yaml` | Twitch bot + Cloudflare Tunnel |
| `observability` | `observability/namespace.yaml` | Loki, Prometheus, Grafana, promtail, kube-state-metrics; in-cluster alerts come from k3s-watcher and external reachability is observed by the GCP monitor |
| `minecraft` | `minecraft/minecraft.yaml` (**not** in `_bootstrap`, see note below) | The Minecraft server — **applied and live** |
| `opsbot` | `_bootstrap/00-namespaces.yaml` | Discord remote-control bot (deployment status/restart, Minecraft RCON console) — **applied and live** |
| `kube-system` | k3s | CoreDNS, Traefik, local-path-provisioner, metrics-server |

**Not owned by this repo**: none — the last holdout, `scotty` (Beads web UI), was created/deployed outside this repo's git history and is now **retired** with the beads migration (2026-08-15). Its manifests were removed from this repo; the `scotty` namespace and its hostPath-mounted `.beads/` databases are gone. See AGENTS.md.

**Deliberate rule, stated in `00-namespaces.yaml`'s header**: each namespace is created exactly once, by the manifest set that owns it. `_bootstrap` creates only the namespaces that do not have a dedicated application directory (`pantry-bot` and `opsbot`); `jmusicbot`, `observability`, and `minecraft` create their own. Don't add a namespace to `_bootstrap` that's already created elsewhere — that creates two sources of truth for one object.

## 3. Networking model

- **No Ingress/Traefik routing used yet** (bundled but idle). Every externally-reachable Service is `type: LoadBalancer`, fulfilled by k3s's bundled ServiceLB (klipper-lb), which implements this by running a `svclb-<service>-*` pod that binds the requested port directly on the node's own IP (`192.168.40.208`) via `hostPort`.
- **Minecraft reaches players via a router port-forward, not a tunnel.** (Corrected mid-migration — the user does *not* use playit.gg. `playit.service`, a leftover from before the router-level forward existed, has since been stopped and disabled — `k8s-homelab-dsg`, closed.) The router forwards external `25565` traffic to `192.168.40.208:25565` — i.e. directly at the node. No in-cluster component needs to know this; the `minecraft` LoadBalancer Service binding that port is sufficient.
- **pantry-bot is reached via Cloudflare Tunnel**, not a LoadBalancer — `cloudflared` in the `pantry-bot` namespace makes only outbound connections to Cloudflare's edge and forwards to pantry-bot's in-cluster `ClusterIP` Service. **Manual step required at cutover, not automatic**: the tunnel's ingress rule (configured in Cloudflare's dashboard, outside this cluster) currently points at the old Docker service name `http://bot:3000` and must be changed to `http://pantry-bot.pantry-bot.svc:3000`.
- **Everything else is `ClusterIP`-only**, reachable in-cluster via standard k8s DNS: `<service>.<namespace>.svc` (or `.svc.cluster.local`).

### Externally-reachable ports (real production ports, post-cutover)

| Port | Protocol | Service | Reaches |
|---|---|---|---|
| `25565` | TCP | `minecraft.minecraft` (LoadBalancer) | Minecraft, via router port-forward |
| `3002` | TCP | `grafana.observability` (LoadBalancer) | Grafana dashboard |
| (Cloudflare Tunnel, no port) | — | `pantry-bot.pantry-bot` (ClusterIP, via `cloudflared`) | pantry-bot's HTTP/WS API |

**Important — these three LoadBalancer ports will show `EXTERNAL-IP <pending>` and their `svclb-*` pod will CrashLoopBackOff on first apply**, because Docker Desktop's containers are still holding `3001`/`3002`/`25565` on the host. This is expected, documented per-manifest, and resolved by the two-pass / verify-then-cutover pattern described in section 6. Don't debug it as a fault.

**Confirmed for `observability`, live**: Grafana (`3002`) and Uptime Kuma (`3001`) both bound `EXTERNAL-IP 192.168.40.208` cleanly on first apply (`kubectl -n observability get svc`) — the old Docker containers for both were stopped before the Services were applied. Minecraft's `25565` is also live through the k3s LoadBalancer Service; `msh.service` is inactive and the router port-forward targets the reserved host address.

### In-cluster-only ports (ClusterIP)

| Service DNS name | Port | Purpose |
|---|---|---|
| `loki.observability` | 3100 (http), 9095 (grpc) | Log storage; Grafana datasource + promtail push target |
| `prometheus.observability` | 9090 | Metrics; Grafana datasource |
| `kube-state-metrics.observability` | 8080 (metrics), 8081 (telemetry) | k8s object-state metrics, scraped by Prometheus |
| `promtail.observability` | 9080 | promtail's own metrics, scraped by Prometheus |
| `pantry-bot.pantry-bot` | 3000 (http), 8080 (ws) | pantry-bot's API, reached by `cloudflared` |
| `minecraft-rcon.minecraft` | 25575 | RCON — deliberately ClusterIP-only, **never** exposed externally (plaintext, password-gated admin channel) |

### Pre-cutover verification ports (temporary, NodePort — removed after cutover)

Docker Desktop holds `3001`/`3002`/`25565` on the host until each old container is stopped. These let the new pods be verified *before* that happens, without fighting over the real port:

| NodePort | Verifies | Manifest |
|---|---|---|
| `30002` | Grafana | `observability/grafana-verify-nodeport.yaml` |
| `30001` | Uptime Kuma | `observability/uptime-kuma-verify-nodeport.yaml` |
| `30565` | Minecraft | `minecraft/minecraft.yaml` (`minecraft-nodeport` Service) |

(There's also `observability/loki-external-nodeport.yaml` — Loki has no host-port conflict since it was never published to the host under Compose, so check that file for why it exists before assuming it's the same pattern.)

### Firewall

`ufw` is enabled with `default allow routed` plus explicit allows for `10.42.0.0/16` (pod CIDR), `10.43.0.0/16` (service CIDR), `6443/tcp` (k8s API), and the LAN-facing ports above. This was a **hard blocker found in review** — the default `DEFAULT_FORWARD_POLICY=DROP` would have silently blackholed all pod-to-pod networking (Docker was exempt via its own `DOCKER-USER` chain; k3s/flannel gets no such exemption).

## 4. Workloads

Every stateful app uses `Deployment` + `strategy: Recreate` + a `ReadWriteOnce` PVC — never `StatefulSet`, never `RollingUpdate` on a stateful app. This is the core safety pattern of the whole design: `Recreate` guarantees the old pod is fully terminated and its volume released *before* the replacement pod mounts the same PVC, which is what prevents two processes from ever opening the same SQLite file, Loki WAL, Prometheus TSDB, or embedded MariaDB datadir at once.

**One deliberate exception as of #177/#180**: `pantry-bot` no longer uses a PVC at all — its storage is now an `emptyDir`, restored from a Litestream/R2 replica by an `initContainer` on every start. This happened specifically because the cluster grew a second node (`pantry-bot-oracle`, arm64, `chayzx/pantry-bot-infra`) and a `local-path` PVC pins a pod to whichever node created it, which would have made cross-node failover impossible. `Recreate` is still used, for the same single-writer reason as always — it now protects the Litestream sidecar's stop/restore handoff instead of a shared PVC mount. See the `pantry-bot` namespace section below and `40-deployment.yaml`'s own comments for the full detail. This pattern (emptyDir + external-replica restore instead of a PVC) is worth considering for any other stateful app that might someday need to run across both nodes — it isn't pantry-bot-specific in principle, just the only one that's needed it so far.

### `jmusicbot` namespace

**Live status: fully migrated and verified, as of this writing.** Both Deployments `1/1 Running`. `jmusicbot`'s logs confirm `serversettings.json loaded`, `YouTube access token refreshed successfully`, `Login Successful!`, `Finished Loading!` — the PVC data migration and the OAuth token both survived, no Discord re-auth was needed. `jmusicbot-release-notifier`'s log shows `Last seen release: v0.7.0`, matching `last_release.json` on the pre-migration host path — its state survived too. Old Docker containers for this stack are stopped and no longer present in `docker ps`.

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `jmusicbot` (Deployment, Recreate) | `ghcr.io/chayzx/jmusicbot:upstream-0.7.0-3e3116039c64` — published multi-arch image; GitHub Actions updates the immutable release tag. | none (outbound Discord gateway only) | 500m/2000m CPU, 512Mi/1Gi mem | `10001:10001` | `jmusicbot-sa`, no API access | `emptyDir` restored/synced through R2 |
| `jmusicbot-release-notifier` (Deployment, Recreate) | `ghcr.io/chayzx/jmusicbot-release-notifier:latest` (public image, no pull secret needed) | none | 50m/100m CPU, 64Mi/128Mi mem | root (image default, not forced) | `jmusicbot-release-notifier-sa`, no API access | `jmusicbot-notifier-data` (256Mi) |

**jmusicbot is managed by the GitHub Actions release workflow.** The workflow builds the patched multi-arch image, publishes it to GHCR, applies the immutable release tag, and verifies the health endpoint. The former cron-driven local `scripts/auto-update.sh` authority was retired 2026-09-09 so a local image cannot overwrite a cloud-recoverable deployment.

### `pantry-bot` namespace

**Live status: applied and live, cut over 2026-08-11; storage migrated PVC→emptyDir 2026-09-06 (#177/#180).** All manifests applied, `pantry-bot` and one `cloudflared` replica per node `Running`, real production data migrated originally (zero-downtime `better-sqlite3` backup from the live Docker container) and again onto the new storage model, verified with a real cross-node failover test onto `pantry-bot-oracle` (row counts matched exactly). Cloudflare Tunnel fully repointed at the k8s Service. Old Docker containers referenced below (`pantry-bot-bot-1` etc.) are long gone — that paragraph is left for historical record of the original migration, not current state.

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | Storage |
|---|---|---|---|---|---|---|
| `pantry-bot` (Deployment, Recreate) | `ghcr.io/chayzx/pantry-bot:latest` (**private**, needs `imagePullSecrets: ghcr-pull-secret`; multi-arch amd64+arm64 as of `pantry-bot#128` — see #127 for why this matters) | 3000 (http), 8080 (ws) | 100m/250m CPU, 128Mi/256Mi mem | `1000:1000` (verified) | `pantry-bot-sa`, no API access | `emptyDir`, restored from R2/Litestream by `restore-db` initContainer on every start — **not** a PVC as of #177/#180. `pantry-bot-data` PVC still exists, orphaned, kept as a fallback — see `30-pvc.yaml.retired`. |
| `cloudflared` (Deployment, RollingUpdate — safe here, stateless) | `cloudflare/cloudflared:latest` | 2000 (metrics/`/ready`) | 50m/200m CPU, 64Mi/128Mi mem | `65532:65532` (nonroot, verified) | `cloudflared-sa`, no API access | none. 2 replicas, pod anti-affinity (one per node) as of #178/#179 — was 1 replica when this table was first written. |

**Keel annotations are gone — this paragraph is historical.** `pantry-bot` originally carried `keel.sh/*` annotations reproducing Watchtower's poll-and-redeploy behavior. Keel itself was retired repo-wide (see `DEPLOYING.md`'s "Previous Auto-Deploy System" note); deploys now run automatically on every merge to `main` through `pantry-bot`'s own consolidated `deploy.yml` (`kubectl set image` + `rollout restart` + `rollout status`) — a merge is the approval step, not a second manual click. See `DEPLOYING.md`'s trigger matrix.

**Probes**: readiness now uses `httpGet: /ready` (the app grew a real `/ready` route since this was first written), not the original exec-based `node -e "require('http').get(...)"` check. Startup and liveness still use the exec form deliberately — see `40-deployment.yaml`'s own probe comments for exactly why each one is shaped the way it is; don't consolidate them to `httpGet` without reading that first.

### `observability` namespace

**Live status, as of 2026-09-06**: the five remaining workloads — `loki`,
`prometheus`, `grafana`, `promtail` (DaemonSet), and `kube-state-metrics` — are
healthy. Uptime Kuma was decommissioned; k3s-watcher handles in-cluster
alerting and the independent GCP monitor handle external reachability. The old Docker promtail
container is deliberately still running alongside the new one until Minecraft
migrates; don't stop it early.

| Workload | Image (pinned) | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `loki` (Deployment, Recreate) | `grafana/loki:3.3.2` | 3100 (http), 9095 (grpc) | 50m/500m CPU, 128Mi/512Mi mem | `10001:10001` | `loki-sa`, no | `loki-data` (20Gi) |
| `prometheus` (Deployment, Recreate) | `prom/prometheus:v3.1.0` | 9090 | 100m/1000m CPU, 512Mi/1Gi mem | `65534:65534` (nobody) | `prometheus-sa`, **yes** (`kubernetes_sd_configs`, scrapes kubelet/cAdvisor) | `prometheus-data` (20Gi) |
| `grafana` (Deployment, Recreate) | `grafana/grafana:12.4.2` | 3000→3002 externally | 50m/500m CPU, 128Mi/512Mi mem | `472:472` | `grafana-sa`, no | `grafana-data` (2Gi) |
| `promtail` (**DaemonSet**, not Deployment — log shipper needs one pod per node) | `grafana/promtail:3.3.2` | 9080 | 50m/200m CPU, 64Mi/256Mi mem | `0:0` (root, required — container logs under `/var/log/pods` are root-owned) | `promtail-sa`, **yes** (`kubernetes_sd_configs`, role: pod) | none (hostPath positions file at `/var/lib/promtail` instead) |
| `kube-state-metrics` (Deployment, RollingUpdate — stateless) | `registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.13.0` | 8080, 8081 | 10m/100m CPU, 32Mi/128Mi mem | `65534:65534` | `kube-state-metrics-sa`, **yes** (lists/watches nearly every object type, read-only) | none |

**Historical note — Uptime Kuma (decommissioned 2026-09-06):** the following
capability and storage notes document the retired migration and are retained as
provenance only:
1. `CAP_CHOWN`/`CAP_FOWNER`/`CAP_SETUID`/`CAP_SETGID` — needed for the outer Node.js process's own `chown`/`chmod` on the MariaDB datadir, and for `mariadbd`'s internal privilege-drop to UID 1000 (`--user=node`). Sourced from the actual uptime-kuma and MariaDB code, not guessed — see the manifest's own header comment for exact file/line citations.
2. A stale root-owned `mysqld.pid` left over from an earlier crash attempt (before fix #1 landed) sat inside an otherwise-correctly-1000:1000-owned `/app/data/run/` — cleared via a temporary debug pod, not a manifest change (it was leftover live data, not a config problem).
3. `CAP_DAC_OVERRIDE` — the piece the original analysis explicitly (and reasonably, for what it analyzed) left out. It correctly reasoned `mariadbd`'s own bootstrap never needs it, but missed that the *outer* Node.js process — which stays root throughout and never drops privilege, unlike `mariadbd` — separately connects to `/app/data/run/mariadb.sock` as a MySQL client. That socket is owned `1000:1000`, root isn't a member of group 1000 here, and "other" permissions are `r-x` (no write) — so a capability-stripped root process fails `connect()` with `EACCES` exactly like a normal non-root user would. Real/unconfined root only bypasses this via `CAP_DAC_OVERRIDE`.

Final capability set: `add: ["CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE"]`. Confirmed healthy end-to-end: pod `1/1 Running`, 0 restarts, `http://192.168.40.208:3001` returns a real response, and its pre-migration monitor configuration survived (existing "Discord Music Bot"/"Twitch Bot" monitors are visible and correctly show the already-documented `docker.sock`-unavailable warning — a known, separate follow-up item, not a new bug).

**Dropped from the old Compose stack, deliberately, not an oversight**: standalone `cadvisor` (kubelet exposes the same data natively at `/metrics/cadvisor` — Prometheus scrapes that instead; also removes the one `privileged: true` container from the whole stack) and a `node-exporter` DaemonSet (a **native host `node_exporter` already runs on `:9100`** — a DaemonSet would collide with it; Prometheus scrapes the existing binary as a static target at `192.168.40.208:9100` instead).

**HDD-specific tuning, present because `/` is spinning rust, not an SSD**: Prometheus retention shortened to 10d/15GB and `--storage.tsdb.max-block-duration=6h` (small frequent compactions instead of rare huge ones that would stall alongside Minecraft's chunk I/O); every stateful store here gets a generous `startupProbe` (Loki: 10 min, Prometheus: 15 min, Uptime Kuma: 10 min) so WAL replay / MariaDB recovery after an unclean stop has time to finish before liveness starts killing the pod.

**Log retention, audited and fixed during cutover**: Loki's `limits_config.retention_period` is `720h` (30d), enforced by `compactor.retention_enabled: true` — both keys are required together or nothing is actually deleted. Loki is the durable, retention-bounded long-term copy; pod stdout on disk is separately bounded by kubelet's own rotation (`containerLogMaxSize`/`containerLogMaxFiles`, currently upstream defaults of 10Mi×5 — a tightened `container-log-max-files=3`/`container-log-max-size=10Mi` is researched and documented in `_bootstrap/LOG-ROTATION.md` but **not yet applied live**, since it needs a deliberate `sudo systemctl restart k3s`, not a side effect of an unrelated change). Minecraft's container image uses a **stdout-only** `log4j2.xml` (Paper's own bundled config with just the file-rolling appender removed, verified byte-for-byte against the live jar) rather than also writing an unbounded `logs/latest.log` to the world PVC — confirmed live that the bare-metal setup had 102 rotated `.log.gz` files going back to March with zero pruning, which would have been genuine unnecessary duplication once Loki is the durable copy anyway.

**promtail carries two hostPath mounts, both required**: `/var/log/pods` (standard k8s container logs) *and* `/home/chase/minecraft/logs` (the still-bare-metal Minecraft server, which doesn't move into the cluster until Phase 4 of the migration — removing this mount early blackholes Minecraft's logs for the whole migration window).

`observability/README.md` has the full apply order, the verification-only-file list, the 3001/3002 cutover sequence, and all four `chown` command blocks paired with their `cp -a`/PV-path-lookup steps.

**ConfigMap ownership, resolved**: `observability/grafana-provisioning.yaml` is authoritative for the two provisioning ConfigMaps (`grafana-provisioning-datasources`, `grafana-provisioning-dashboards`) — these are what `grafana.yaml`'s volume mounts reference. Dashboard *content* is the generated ConfigMap `grafana-dashboards`, sourced from the checked-in `dashboards/dashboards-configmap.yaml`; the source JSON and generated copy are validated together by `scripts/validate-grafana-dashboards.sh`.

### `minecraft` namespace

**Live status: applied and live, migrated.** `msh.service` is `inactive` (confirmed via `systemctl is-active`); the `minecraft` LoadBalancer Service is bound at `192.168.40.208:25565` and the `minecraft` pod is `1/1 Running`. `playit.service` has been stopped and disabled — the runbook's last cleanup step (`minecraft/MIGRATION.md` Step 10) is now complete (`k8s-homelab-dsg`, closed).

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `minecraft` (Deployment, Recreate) | `ghcr.io/chayzx/paper-minecraft:26.2-b121-e1fe13e5a7b5` — published immutable image, verified live | 25566/tcp (game), 25566/udp (query), 25575/tcp (rcon) | requests 1 CPU/2Gi mem, limits 2 CPU/5Gi mem | `1000:1000` | `minecraft-sa`, no API access | `minecraft-world` (10Gi) |

**Heap: `-Xms2G -Xmx3G`** plus explicit `-XX:MaxDirectMemorySize=512M -XX:MaxMetaspaceSize=256M`; the current deployment limits the pod to 5GiB. This keeps JVM heap and native regions bounded without claiming that an OOM can be recovered safely mid-save.

**No liveness probe, by design.** Only a `readinessProbe` (`tcpSocket: 25566`, generous timing — up to ~5.5 min startup grace for a 1.3GB world loading off a 5900rpm HDD from a cold page cache). A Paper server that's laggy under a world-gen burst or a chunk-save spike is *alive* and will recover; a livenessProbe would SIGKILL it mid-tick and risk the world with it.

**`terminationGracePeriodSeconds: 120`** (not the 30s default) — not enough time to flush a 1.3GB world on a spinning disk otherwise, which risks corruption. Shutdown path: `preStop` runs an RCON `save-all flush` + `stop` via a small helper JVM (`rcon.jar`, `-Xmx32m`), falling through non-fatally to `SIGTERM` → the JVM's own shutdown hook (java is PID 1, verified) if RCON fails for any reason.

**Two-pass apply, controlled by the label `homelab.chase/cutover-stage`**: everything except the production `minecraft` LoadBalancer Service is labeled `pre` and safe to apply anytime; the LoadBalancer Service alone is labeled `cutover` and **must not be applied while `msh.service` still owns port 25565 on the host** — doing so makes ServiceLB install a hostPort DNAT rule that hijacks live player traffic into a pod that may not even be ready yet. See `minecraft/MIGRATION.md` for the exact sequencing (written and present in the repo, not pending).

`minecraft-rcon` is a `ClusterIP` Service (never external — RCON is a plaintext, password-gated admin channel) reached at `minecraft-rcon.minecraft.svc.cluster.local:25575` by `scripts/minecraft_backup.py` and `scripts/minecraft_exporter.py`.

`minecraft/MIGRATION.md` is the full 10-step serial cutover runbook — both corrections are folded in (no playit.gg, router port-forward instead; `msh.service` must stop before the NodePort isolation test, not just before the final port flip, since the node can't hold both the old 12G-heap JVM and the new pod's memory at once). Highlights worth knowing without re-reading the whole thing:
- **The build tag `26.2-b121-e1fe13e5a7b5` is pinned to the exact published live image** — a future Paper upgrade is a deliberate, reviewable tag bump, not a silent `:latest` pull. GitHub Actions is the image/deployment authority.
- **`entrypoint.sh` refuses to start against an empty/unmounted data dir** — without this guard, a botched PVC mount would let Paper silently generate a brand-new world and pass its readiness probe, looking like a successful migration while players spawn into nothing and the real world sits untouched on the old disk.
- **Rollback has three distinct cases** depending on whether a player has joined the new pod yet (pre-flip: free; post-flip-no-player: free; post-player-join: restarting `msh` **silently reverts to the frozen old world with zero error message** — the runbook's recovery path is to promote the PVC's current state back to bare-metal instead of reverting, so nothing played is lost).
- `playit.service` gets disabled as the very last cleanup step, only after the new Service is confirmed bound and joinable — not before. **Done**: stopped and disabled 2026-08-12 (`k8s-homelab-dsg`, closed).

**Automatic Paper build updates**: `scripts/minecraft-auto-update.sh` runs from the tower user's 03:00 cron. It auto-deploys new **STABLE** builds of the pinned Minecraft version only (patch/build), self-rolls back via `kubectl rollout undo` on a failed rollout or failed post-deploy RCON check. Major/minor `MC_VERSION` bumps stay a manual script edit, never automatic. Full mechanism, policy, and rollback detail: `minecraft/MIGRATION.md`'s "Post-migration: automatic Paper build updates" section.

### `keel` namespace (REMOVED 2026-08-13)

**Retired.** Live/applied 2026-08-11 through 2026-08-13; decommissioned as part of `k8s-homelab-oiv` (migrate CI/CD to GitHub Actions). Real incident (`k8s-homelab-9nb`, closed as superseded): Keel's separate registry credential silently expired 2026-08-11, deploys stopped for a full day with zero alerting — the exact failure mode a background auto-updater with its own credential path is prone to. Replaced at the time by `publish.yml` (automatic build) + `deploy.yml` (manual-click `workflow_dispatch`) reaching the cluster through `ci-tunnel/` — **that two-stage split is itself since retired**; `deploy.yml` is now one consolidated workflow that builds, applies, restarts, and verifies automatically on every merge to `main` (no second click — see `DEPLOYING.md`'s trigger matrix and `pantry-bot/40-deployment.yaml`'s header). Namespace deleted, `keel/` manifests removed from this repo, `_bootstrap/00-namespaces.yaml` no longer creates it. The paragraphs below are kept as historical record of the design (digest-pinning a floating tag, the two-credential failure mode) — none of it is live anymore.

<details>
<summary>Historical design notes (Keel, retired)</summary>

Deployment `1/1 Running`, zero errors while live. `keel-registry-creds` was created by reusing `ghcr-pull-secret`'s existing PAT (same credential is valid for both consumers per this file's own guidance above — no new secret was minted). Live testing found the RBAC comment below was wrong about ungranted resource kinds being silently ignored; fixed (read-only grants added for statefulsets/daemonsets/cronjobs, comment corrected in the manifest itself).

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `keel` (Deployment, Recreate) | `ghcr.io/keel-hq/keel@sha256:b76f24cc17a69ba4b0c15f81641fa4bfa18b7400e6ae416e478fcdd86ae46569` — pinned by **digest**, not tag; see below for why | 9300 (UI, unused by this design — no Service exposes it) | 50m/100m CPU, 64Mi/128Mi mem | `666:666` (image's own built-in non-root user, verified via `docker image inspect`) | `keel-sa`, **yes**, cluster-scoped (the one workload that needs it) | none (emptyDir for its internal sqlite state — not worth persisting, Keel rebuilds its view from the API) |

**Why a digest pin instead of the tagged `0.21.1` release**: verified directly against `keel-hq/keel`'s commit log that a real fix for polling *mutable* tags (`fix(poll): seed digest watcher from the running image digest`, commit `2f245c5`, 2026-08-10) landed on `master` **after** the 0.21.1 release — and `:latest` (what pantry-bot used) is exactly a mutable tag. Since no newer versioned release existed yet, the `master` floating tag was resolved to a specific digest confirmed (via the image's own `org.opencontainers.image.revision` OCI label) to be built from a commit *after* the fix. Recorded as a deliberate tradeoff, not a clean answer — `master` is unversioned CI output with no changelog guarantee, and a digest pin means Keel wouldn't self-update; it needed manual re-pinning periodically.

**RBAC** (`keel/20-clusterrole-clusterrolebinding.yaml`, removed) was cluster-scoped by necessity (the Deployment it watched, `pantry-bot`, lived in a different namespace) but deliberately trimmed from keel-hq/keel's own upstream Helm chart default — no blanket `delete`, no StatefulSet/DaemonSet/Job access this home-lab didn't use.

**Two separate credential paths, explicit not automatic**: Keel used its own `DOCKER_REGISTRY_CFG` env var (from a `keel-registry-creds` Secret) to poll the GHCR API, rather than relying on its automatic `imagePullSecrets`-discovery path — the automatic path depends on internal pod-selector matching and fails *silently* (no update, no error) if it ever breaks. This exact silent-failure class is what `k8s-homelab-9nb` hit and what motivated retiring Keel.

</details>

### `opsbot` namespace

**Live status: applied and live**, deployed for the first time this session (pod `1/1 Running`, deployment `opsbot`). A standalone Discord bot giving remote, phone-friendly control over specific homelab workloads — `/pods status` and `/deploy status`/`/deploy restart` on an explicit allowlist (`jmusicbot`, `pantry-bot`, `minecraft`), plus a Minecraft RCON console bridge. The bot makes only an OUTBOUND connection to Discord's gateway; no new inbound network exposure. Standalone rather than bolted onto jmusicbot, which is a Java bot with its own fragile OAuth/build pipeline, not a general plugin host.

| Workload | Image | Ports | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|
| `opsbot` (Deployment, RollingUpdate — stateless, no PVC) | `localhost/opsbot:dev` — locally built, side-loaded via `docker save ... \| sudo k3s ctr images import -`, never pulled | none (outbound Discord gateway only) | see manifest | `opsbot-sa`, **yes** — one of this repo's rare exceptions, its whole job is calling the k8s API on the user's behalf | none |

**RBAC** (`opsbot/20-rbac.yaml`) is deliberately namespaced `Role`+`RoleBinding` pairs, NOT a ClusterRole (contrast with Keel, which has to discover annotated Deployments anywhere in the cluster without a namespace list — opsbot's requirement is the opposite: an explicit, closed allowlist):

| Namespace | Resource | Verbs |
|---|---|---|
| `jmusicbot`, `pantry-bot`, `minecraft` | `pods` | `get`, `list`, `watch` |
| `jmusicbot`, `pantry-bot`, `minecraft` | `deployments` (apps) | `get`, `list`, `watch`, `patch` |
| `minecraft` only | `pods/exec` | `create` (RCON bridge — reaches `rcon.jar` inside the running minecraft pod; kept as a separate Role so this broader grant doesn't silently ride along in the other two namespaces' identical copies) |

Excluded on purpose: `keel` and `observability` namespaces (not on the whitelist), any `secrets` verb, `delete`/`deletecollection`, and anything cluster-scoped. The `deployments patch` verb is deliberately sufficient (not `update`/`create`) — a `/deploy restart` does exactly what `kubectl rollout restart` does under the hood: fetch the Deployment, PATCH `spec.template.metadata.annotations` to stamp a restart timestamp, and let the Deployment controller's own ReplicaSet privileges handle the rest.

**Redeploy**: `kubectl apply -f _bootstrap/00-namespaces.yaml -f opsbot/10-serviceaccount.yaml -f opsbot/20-rbac.yaml -f opsbot/40-deployment.yaml` (namespace, then identity, then RBAC, then workload — the `opsbot-discord` Secret with `DISCORD_BOT_TOKEN`/`DISCORD_USER_ID` is created imperatively, never in git; see `opsbot/SECRETS.md`). Full design rationale and current work are tracked in GitHub Issues.

## 5. Storage layout

All PVCs use `storageClassName: local-path` (pinned explicitly in every manifest, not left to the cluster default — so a future default-StorageClass change can't silently strand a PVC). `local-path-provisioner` uses `WaitForFirstConsumer` binding, so a PVC shows `Pending` until a pod that mounts it is actually scheduled — expected, not a fault. Once bound, find the real host directory with:

```bash
PVC_NAME=<name>; NS=<namespace>
PV=$(kubectl -n "$NS" get pvc "$PVC_NAME" -o jsonpath='{.spec.volumeName}')
kubectl get pv "$PV" -o jsonpath='{.spec.local.path}'
```

| PVC | Namespace | Size | Container UID it must be `chown`'d to |
|---|---|---|---|
| `jmusicbot-config` | `jmusicbot` | 1Gi | `10001:10001` |
| `jmusicbot-notifier-data` | `jmusicbot` | 256Mi | `0:0` (root, image default) |
| `pantry-bot-data` (**retired, not mounted by any pod as of #177/#180** — orphaned on purpose, kept as a fallback, not live storage) | `pantry-bot` | 1Gi | `1000:1000` |
| `loki-data` | `observability` | 20Gi | `10001:10001` |
| `prometheus-data` | `observability` | 20Gi | `65534:65534` |
| `grafana-data` | `observability` | 2Gi | `472:472` |
| `minecraft-world` | `minecraft` | 10Gi | `1000:1000` |

**Every one of these UID/GID pairs was verified against the actual running container** (`docker top <container> -o user,group` or equivalent) during manifest-writing, not assumed from the base image — this mattered because the pre-migration data on disk is uniformly owned `1000:1000` (Docker Desktop's virtiofs fakes ownership on bind mounts; bare-metal k3s enforces real UIDs), so several of these need an explicit `chown` after the data copy, or the pod crash-loops on `EACCES`. Each manifest carries the exact `chown` command in its own header comment.

## 6. RBAC model

- **Every workload gets its own dedicated ServiceAccount**, named `<app>-sa`, namespaced with the app. `automountServiceAccountToken: false` is set explicitly wherever the app has no legitimate reason to call the Kubernetes API — which is most of them. Workloads that DO need API access get a narrow, purpose-built Role: `prometheus-sa` (service discovery + kubelet/cAdvisor scraping), `promtail-sa` (pod discovery for log labeling), `kube-state-metrics-sa` (broad read-only listing — that's its whole job), `opsbot-sa` (pods/deployments in an explicit namespace allowlist, see its own section), and `ci-deploy` (deployments/replicasets only, one per deployed namespace, used by GitHub Actions — see `ci-deploy/README.md`). `keel`'s SA is gone along with the rest of Keel (removed 2026-08-13).
- **`homelab-admin`** is a `ClusterRole` (`_bootstrap/10-homelab-admin-clusterrole.yaml`) bound to a *named human* kubectl identity, not to any group every pod lands in. It's the union of everything needed to manage this home-lab day to day: full CRUD on ServiceAccounts/Services/ConfigMaps/Secrets/PVCs/Deployments/Roles/RoleBindings, plus read access and pod delete/exec/port-forward for debugging. Its own header comment records the honest caveat: this combination is *functionally* equivalent to cluster-admin (create-Deployment + create-ServiceAccount + read-Secret lets the holder mount any token in the cluster), so it's a convenience/audit-trail boundary for a trusted operator, not a security boundary against a hostile one.
- **Explicitly rejected, and recorded as a deliberate decision** (not an oversight to "fix" later): granting `system:authenticated` — or any group every pod's ServiceAccount lands in by default — edit rights on ServiceAccounts, Roles, RoleBindings, or Secrets. That's a textbook pod-to-cluster privilege-escalation path (a compromised pod could mint or repoint a ServiceAccount and inherit its permissions). The user was asked directly and chose against it.

## 7. Cross-cutting patterns worth knowing before touching anything

- **`imagePullPolicy` is load-bearing, not a style choice, on every locally-built image** (`jmusicbot`, `minecraft`). It must be `IfNotPresent` — these images are side-loaded via `docker save <tag> | sudo k3s ctr images import -` and exist in **no registry**; `Always` (or the implicit default on a `:latest` tag) sends the kubelet looking for a registry that doesn't have them, and the pod sits in `ErrImagePull` forever.
- **`fsGroupChangePolicy: OnRootMismatch`** appears on every PVC-backed pod's `securityContext`, deliberately not left at the `Always` default — `Always` recursively `chown`s the *entire volume* on every single pod start, which on the HDD means minutes of I/O contention with Minecraft before the app even starts. `OnRootMismatch` checks only the volume root and skips the walk once ownership is already correct.
- **Startup probes are generous everywhere data has to load off the HDD** (Loki WAL replay, Prometheus WAL replay, Uptime Kuma's MariaDB recovery, Minecraft's world load) — the pattern is a long `startupProbe` gating a much stricter `readinessProbe`/`livenessProbe`, so a slow-but-healthy cold start can't be mistaken for a crash loop.
- **Config-revision annotations** (`homelab/config-revision` or `homelab.local/config-revision`) appear on several pod templates specifically because editing a ConfigMap does **not** automatically restart the pods consuming it — bumping this annotation (or `kubectl rollout restart`) is the documented way to pick up a config change.

## 7a. Dashboards (`dashboards/`)

Four dashboards, all built, embedded in the `grafana-dashboards` ConfigMap (`dashboards/dashboards-configmap.yaml`, ~151KiB on disk as of this re-verification — grown a bit since it was last measured, still well under the 1MiB ConfigMap ceiling; confirmed live via `kubectl -n observability get configmap grafana-dashboards`). All use `${DS_PROMETHEUS}`/`${DS_LOKI}` datasource variables rather than hardcoded UIDs, so they import cleanly regardless of what UID a given Grafana instance assigns.

**Resolved, but worth knowing this happened**: Grafana's own database (separate from this ConfigMap, UI-editable) had accumulated **four legacy dashboards** inherited from the old Docker Grafana's persisted DB during the observability PVC migration — `host-overview`, `logs-overview`, `container-resources`, `minecraft` — none provisioned from this repo, invisible to any file-based fix. All four used legacy Docker cAdvisor labels (`container_cpu_usage_seconds_total{name!=""}` etc, raw container-ID strings) and were fully superseded by the four dashboards above. Deleted via Grafana's own API (`k8s-homelab-wp8`, closed). The credential blocker that stalled this across two investigation rounds was a wrong-username assumption, not a broken password reset — the real admin login on this inherited DB is `chasepdrsn` (matching the account owner's email), not the default `admin`. Worth remembering if Grafana auth ever seems broken again: check the actual `user` table (`sqlite3` against a copied-out `grafana.db`) before assuming credentials are wrong.

| Dashboard | Covers |
|---|---|
| `cluster-overview.json` | Node-level health from the native `node_exporter` (CPU/mem/disk/load), explicit HDD I/O latency/utilization panels (root is a 5900rpm disk — Prometheus compaction, Loki flush, and Minecraft saves all contend for it), k3s Node-Ready status, memory breakdown with reference lines at total capacity (~15Gi) and the `system-reserved=3Gi` floor |
| `pods-and-workloads.json` | Per-namespace/per-pod usage **plotted against configured limits** (the plan's specific ask) — memory and CPU, both as absolute usage and %-of-limit bar gauges (red above 85%), restart counts, OOMKill events (cross-checked against two independent metrics since `container_oom_events_total` is known-unreliable on some runtimes), Deployment replica health. One flagged, self-resolving TODO: a PVC-usage panel needs `kubelet_volume_stats_*` from the kubelet's main `/metrics` endpoint — confirmed present via the `kubernetes-kubelet` scrape job in `prometheus-config.yaml` (added independently by the observability agent), so this isn't actually a gap. |
| `logs.json` | **The severity-filtering deliverable.** A `level` template variable (multi-select debug/info/warn/error) wired directly into every LogQL stream selector, so filtering happens at Loki's index/chunk level, not client-side. Plus namespace/pod drill-down, free-text search, a log-volume-by-severity graph, an always-on error count/panel that ignores the level filter (so "how many errors right now" never depends on a dropdown state), and an "unclassified lines" counter that catches any app whose promtail parsing stage breaks or was never written. |
| `apps.json` | Minecraft (real RCON-based metrics: TPS, players, world size, server-up; memory via cAdvisor plotted against the 5Gi limit and 3Gi heap ceiling so JVM overhead is visually distinct from real growth), jmusicbot and pantry-bot (both honestly limited to pod-status/restarts/memory-vs-limit — neither has real app metrics; pantry-bot's "health" panel is `kube_pod_status_ready`, not real HTTP health, with the panel description saying so explicitly rather than inventing a metric). |

**How the severity filter actually works, mechanically**: promtail's `pipeline_stages` (`observability/promtail-config.yaml`) extract a `level` value per line via regex/JSON and promote it to a real Loki **label** via a `labels:` stage — not just a parsed field. That's what makes `{namespace=~"$namespace", level=~"$level"}` work as a stream selector Loki resolves before reading log bodies, rather than a slow client-side text scan. If a given app's parsing stage ever breaks, its logs don't vanish — they fall into "unclassified" (a dedicated stat panel exists specifically to catch that).

## 8. Host-level monitoring: k3s-watcher (Discord alerting for restart-loops + log errors)

**Supersedes `_bootstrap/WATCHERS-TODO.md`'s original recommendation** (Alertmanager rules routed through a Discord webhook receiver) — that plan was reconsidered and NOT what got built; see the design tradeoff below for why. `WATCHERS-TODO.md` is left in place for its still-accurate background on why the old bespoke watchers couldn't run under k3s as-is, but its "k8s-native alternative" section describes a path not taken.

Not a k8s workload — a Python script run as a `systemd --user` service directly on the host, the same pattern already established by `scripts/minecraft_backup.py`/`minecraft_exporter.py`. It replaces the Discord DM alerting half of the old Docker-side `observability-watcher` container (source: `github.com/ChayzX/observability`, public, `watcher/watcher.py`) now that container is stopped for good — see the top status flag. Current investigation, design, implementation, and verification history are tracked in GitHub Issues.

| | |
|---|---|
| Script | `/home/chase/docker/observability/monitoring/k3s-watcher/watcher.py` (host path, outside this repo — matches where `minecraft_backup.py`/`minecraft_exporter.py` already live) |
| Self-check | `/home/chase/docker/observability/monitoring/k3s-watcher/test_watcher.py` (cooldown, restart-windowing, regex logic) |
| Config | `/home/chase/docker/observability/monitoring/k3s-watcher/.env` — reuses the existing `DISCORD_BOT_TOKEN`/`DISCORD_USER_ID` pair the same Discord bot already uses for `host_health.py`/`minecraft_backup.py`/`minecraft_exporter.py` DMs. No new Discord app was created. |
| systemd unit | `~/.config/systemd/user/k3s-watcher.service` — `Type=simple`, `Restart=always` (long-running poll loop, not a oneshot/timer) |
| Scope (`WATCH_NAMESPACES`) | `jmusicbot`, `pantry-bot` only — matches the old watcher's `WATCH_CONTAINERS` scope exactly. **Not** extended to `keel`/`minecraft`/`observability` (deliberately scoped out during design rather than silently expanded — see below for how to add them) |

**How it works** (mirrors the old watcher's exact alert triggers, cooldowns, and Discord embed format — `k8s-homelab-2go.1`'s notes hold the byte-for-byte original spec it was matched against):
- **Restart-loop detection**: polls `kubectl get pods -n <ns> -o json` every `POLL_INTERVAL_SECONDS` (default 30s) for both watched namespaces, tracks `restartCount` deltas per namespace/pod/container in a rolling `RESTART_WINDOW_SECONDS` (600s) window, fires at `RESTART_THRESHOLD` (≥3 restarts in the window).
- **Log-error detection**: polls Loki's `/loki/api/v1/query_range` API (ClusterIP `10.43.54.179:3100`, reachable from the host the same way `minecraft_exporter.py`'s `RCON_HOST` fix works — single-node k3s shares the host's network namespace) for `{namespace=~"jmusicbot|pantry-bot"}`, filtered by the same default regex the original watcher used: `error|exception|fatal|disconnected|reconnect(ing)?|stacktrace|failed to` (case-insensitive). Only queries the incremental window since the last poll.
- **Cooldown**: 300s per alert-type-per-container key — exact match to the original, prevents duplicate DMs for the same ongoing issue.
- **Discord format**: bot DM via embeds (not a webhook, so it lands as a DM to `DISCORD_USER_ID`, not a channel post) — `⚠️ {container} log alert` (red) with the matched pattern + log line, `🔁 Restart loop detected` (red) with count/window, `✅ Watcher online` (green) on startup listing watched namespaces.

**Why a host systemd script instead of a k8s CronJob or Grafana/Alertmanager-native alerting** (Loki + a Discord contact point could technically express both halves of this with zero new code — see `WATCHERS-TODO.md`'s original recommendation above): Grafana's Discord integration is webhook-based, which posts to a channel, not a DM to a specific user — it can't replicate the original watcher's actual notification behavior. More importantly, this repo has used a small Python-script-with-bot-DM pattern for Discord alerting three times already (`host_health.py`, `minecraft_backup.py`, `minecraft_exporter.py`) — reusing that established convention outranked reaching for new Grafana/Alertmanager-native infrastructure just for this one case, which would have introduced a second, differently-behaved alerting mechanism. The design tradeoff is retained in this document and the GitHub issue history.

**Proven end-to-end, not just "started cleanly"**: a real error was induced via pantry-bot's actual OAuth callback path (an invalid `code` param against its live `/oauth/callback` route), producing a genuine `console.error` line in the pod. That line reached Loki and the watcher fired a real Discord alert 15 seconds later — confirmed by reading the Discord API's message list directly, not by trusting watcher stdout. Full evidence is retained in the GitHub issue history.

**A dependency this proof needed, worth knowing about**: Loki's entire ingestion pipeline was silently stalled cluster-wide from roughly 01:00–02:32 UTC this session (unrelated root cause — a k3s restart plus a separate promtail DaemonSet restart fixed it), which is why the real end-to-end alert proof above only became possible once that was resolved. Tracked and closed separately as `k8s-homelab-ofq`; see that issue for the incident detail, not repeated here.

**`observability-network-exporter` needed no replacement at all**, k3s-side or otherwise — it existed solely to work around a Docker-Desktop-specific cAdvisor bug misreporting per-container network stats under Docker's VM networking (`pid: host`). Confirmed (`k8s-homelab-2go.2`, closed) that k3s's native kubelet cAdvisor does not share this bug — real, distinguishable per-pod network I/O already works today (see the Pod Network I/O panel in `dashboards/dashboards-configmap.yaml`). Retired with the rest of the Docker-side stack; nothing needed rebuilding on this half.

**To extend scope to keel/minecraft/observability's own health**: add the namespace(s) to `WATCH_NAMESPACES` in the `.env` file above (e.g. `WATCH_NAMESPACES=jmusicbot,pantry-bot,minecraft`) and `systemctl --user restart k3s-watcher` — the polling logic is already namespace-generic, no code change needed. Not done today; deliberately scoped out during design pending a check with the user first (`k8s-homelab-2go.4`).

## 9. Status: live cutover in progress, applied namespace-by-namespace

Every namespace's manifests, the dashboards, the RBAC model, and the script rewrites are written and reviewed (repo `~/k8s-homelab/`, git history, oldest to newest: `44f1db9` initial manifest set → `48e09b9` fix an invalid ConfigMap label → `3804dc5` fix three live cutover bugs + log-retention audit → `ad0c0f6` confirm JVM_OPTS/rsync for stdout-only logging → `5dc0e4b` fix uptime-kuma chown instructions (mariadb/ subdirectory needs its own 1000:1000 chown, not just the whole-PVC one) → `f041581` uptime-kuma: add `CAP_DAC_OVERRIDE`, the fifth and final capability needed → `a49626c` ARCHITECTURE.md: mark uptime-kuma fully resolved. Seven commits total; `git status` is clean — nothing uncommitted. Cutover is being executed by hand per each namespace's `README.md`/`MIGRATION.md`, in the order: Phase 0 remainder (secrets, storage decisions) → observability → jmusicbot → pantry-bot + Keel → Minecraft (highest risk, most novel, done last) → Docker Desktop retired only after a real soak period (**note, corrected below section 1's host table**: in practice this meant retiring the redundant Docker-side observability Compose stack running inside Docker Desktop, not Docker Desktop/`docker-ce` themselves — both remain load-bearing for local image builds).

**Actual progress as of this writing** (freshly re-verified live against the running cluster, not transcribed from a plan or an earlier doc pass): `_bootstrap` done. `observability` fully applied and healthy. `jmusicbot`, `pantry-bot`, `minecraft`, and `opsbot` are live and managed by their current deployment authorities; pantry-bot's production data and Cloudflare Tunnel are on k3s, and Minecraft is served by the k3s LoadBalancer with `msh.service` inactive. `keel`, `scotty`, and the retired Docker observability stack are not live. `playit.service` has been stopped and disabled. Every namespace in this repo's tracked migration scope is applied and healthy; cross-environment recovery work is tracked separately in `HA-EXPANSION.md` and GitHub Issue #191.

This document is meant to stay current, not describe a snapshot — re-verify against `kubectl get ns`/`kubectl get pods -A` before trusting any status line above.
