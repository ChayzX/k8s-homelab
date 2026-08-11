# MinecraftMachine k3s Home-Lab — Architecture Reference

**Read this first.** This document is the single source of truth for how this cluster is built: every namespace, every workload, every port, every Service DNS name, the RBAC model, and the storage layout. It's written so a human or an AI agent can understand the whole system without reading every manifest — but the manifests are still the actual source of truth for exact syntax; this is the map, not the territory.

**Status flag — read before trusting anything below as "live":** cutover is **in progress, applied namespace-by-namespace**, not all-or-nothing. As of this writing (2026-08-11, ~04:20 UTC, freshly re-verified directly against the live cluster):
- **Applied and live**: `_bootstrap`, `observability` (all six workloads `1/1 Running`), `jmusicbot` (fully migrated, old Docker containers stopped), `keel` (applied, healthy, zero errors — see RBAC note below), `pantry-bot` (applied, healthy, real production data migrated, Cloudflare Tunnel fully cut over — see below).
- **Namespace created, nothing else applied**: none remaining.
- **Not started at all**: `minecraft` — no `minecraft` namespace exists yet; `msh.service` is still `active` and still holds port 25565. Highest-risk phase, deliberately last; also blocks the new Minecraft-auto-update epic (`k8s-homelab-338`, Beads).

**Keel** (applied 2026-08-11 ~03:55 UTC): `keel-registry-creds` created by decoding and reusing `ghcr-pull-secret`'s existing PAT, per `keel/SECRETS.md`'s own guidance that the same credential is valid for both consumers — no new secret needed. Live testing surfaced a real gap in `keel/20-clusterrole-clusterrolebinding.yaml`'s own comment: it claimed Keel "silently no-ops" on workload kinds (statefulsets/daemonsets/cronjobs) it isn't granted RBAC for. That's false — Keel's provider layer watches all these kinds unconditionally at startup and spams `reflector.go` "Unhandled Error"/"forbidden" every few seconds if denied. Fixed by adding read-only (`get,list,watch`, no write) grants for those three kinds; comment corrected in place. Zero errors since.

**Pantry-bot** (applied 2026-08-11 ~03:58 UTC): all 6 manifests applied. Two live issues were caught and fixed during cutover, not by pre-apply review:
1. *Split-tunnel traffic*: this Cloudflare Tunnel is dashboard-managed (`TUNNEL_TOKEN` only, no local ingress config) and turned out to serve **four** hostnames on one tunnel, not just pantry-bot's two — `oauth.greeniespantry.uk` + `overlay.greeniespantry.uk` (pantry-bot) and `status.greeniespantry.uk` + `grafana.greeniespantry.uk` (uptime-kuma/grafana), previously routed via `host.docker.internal:3001`/`:3002` from the Docker Desktop side. Any connector of a dashboard-managed tunnel serves *all* its hostnames regardless of which one Cloudflare's edge happens to route a given request to — so bringing up a k8s `cloudflared` connector immediately put it in the same pool as the old Docker one, and whichever connector a request landed on had to resolve the origin from *its own* network namespace. The two origin styles are mutually exclusive: `pantry-bot.pantry-bot.svc:*` only resolves from inside the k3s pod network, `host.docker.internal:*` only resolves from the Docker Desktop side. Fix: `status`/`grafana` origins changed to the stable LoadBalancer IP (`192.168.40.208:3001` uptime-kuma, `:3002` grafana — confirmed reachable from both runtimes), then the old Docker `pantry-bot-cloudflared-1` connector was stopped once the k8s connector was confirmed serving all four hostnames correctly. All four verified externally post-cutover (oauth/overlay `404`-on-root — expected, no root route defined, means reachable; status/grafana `302`).
2. *Empty PVC*: applying the manifests gives pantry-bot a fresh, empty SQLite DB — the real data lives in a separate Docker named volume (`pantry-bot_pantry-data`) that manifest application does not touch. Migrated via a **zero-downtime** consistent snapshot: `better-sqlite3`'s `.backup()` API run inside the still-live Docker container (no interruption to production), copied out through a scratch dir (not the repo, to avoid ever committing production data), `kubectl cp`'d into the new pod's PVC, stale empty `-wal`/`-shm` files cleared, pod restarted. Verified via absence of the "No stored broadcaster/bot credentials" bootstrap message post-restart — real OAuth state loaded correctly.

Old Docker `pantry-bot-bot-1` and `pantry-bot-watchtower-1` containers are still running but no longer receive any traffic (tunnel fully repointed) — left up deliberately as a fallback during the soak period, per this repo's stated Docker-retirement policy. The standalone Docker `promtail` container (mounts `docker.sock` + `/home/chase/minecraft/logs`) is still required for as long as these two plus the Minecraft Docker server exist — it's the only thing shipping their logs to Loki, since k8s promtail only tails pod logs.

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
| NVMe (`/dev/nvme0n1`) | **Not usable** — confirmed to be a dd'd Ubuntu installer image (iso9660 + casper `writable` partition), not free space. Do not repurpose without deliberately deciding to destroy that installer. |
| LAN IP | `192.168.40.208`, interface `wlo1` (WiFi), **DHCP-assigned, no reservation** — open item, not yet fixed at the router |
| GPU | NVIDIA RTX 2060 — used only by the desktop session (Xorg, gnome-remote-desktop's NVENC encoder). Not used by, or available to, anything in this cluster. |
| k3s version | v1.36.3+k3s1, containerd 2.3.2 |
| k3s install flags | `--write-kubeconfig-mode 644 --kubelet-arg=system-reserved=cpu=1000m,memory=3Gi --kubelet-arg=eviction-hard=memory.available<500Mi` — the `system-reserved` flag is why `kubectl get node` shows allocatable memory (~12.0Gi) well below capacity (~15.5Gi): that gap is deliberately held back for the desktop session, which the scheduler would otherwise be blind to. |
| Default StorageClass | `local-path` (k3s-bundled `local-path-provisioner`) — PVCs become plain directories under `/var/lib/rancher/k3s/storage/` on the HDD above |
| Also installed, bundled by k3s | Traefik + its ServiceLB DaemonSet (binds 80/443, unused by anything in this design so far — harmless, costs a little RAM) |
| Native `docker-ce` / `containerd` | **Deliberately left running** alongside k3s (separate containerd instance/socket, no conflict) — `scripts/auto-update.sh` needs a live `dockerd` for local image builds. Docker Desktop (a *third*, VM-based runtime, `desktop-linux` context) is what's being migrated away from and gets retired last. |

## 2. Namespaces

| Namespace | Created by | Owns |
|---|---|---|
| `jmusicbot` | `_bootstrap/00-namespaces.yaml` | Discord music bot + release notifier |
| `pantry-bot` | `_bootstrap/00-namespaces.yaml` | Twitch bot + Cloudflare Tunnel |
| `keel` | `_bootstrap/00-namespaces.yaml` | Auto-update controller for pantry-bot — **applied and live** |
| `observability` | `observability/namespace.yaml` | Loki, Prometheus, Grafana, Uptime Kuma, promtail, kube-state-metrics |
| `minecraft` | `minecraft/minecraft.yaml` (**not** in `_bootstrap`, see note below) | The Minecraft server |
| `kube-system` | k3s | CoreDNS, Traefik, local-path-provisioner, metrics-server |

**Deliberate rule, stated in `00-namespaces.yaml`'s header**: each namespace is created exactly once, by the manifest set that owns it. `_bootstrap` only creates the three it doesn't otherwise have a home for (`jmusicbot`, `pantry-bot`, `keel`); `observability` and `minecraft` create their own. Don't add a namespace to `_bootstrap` that's already created elsewhere — that creates two sources of truth for one object.

## 3. Networking model

- **No Ingress/Traefik routing used yet** (bundled but idle). Every externally-reachable Service is `type: LoadBalancer`, fulfilled by k3s's bundled ServiceLB (klipper-lb), which implements this by running a `svclb-<service>-*` pod that binds the requested port directly on the node's own IP (`192.168.40.208`) via `hostPort`.
- **Minecraft reaches players via a router port-forward, not a tunnel.** (Corrected mid-migration — the user does *not* use playit.gg, despite `playit.service` still being active/enabled on the host; that's an unresolved loose end, not part of this design.) The router forwards external `25565` traffic to `192.168.40.208:25565` — i.e. directly at the node. No in-cluster component needs to know this; the `minecraft` LoadBalancer Service binding that port is sufficient.
- **pantry-bot is reached via Cloudflare Tunnel**, not a LoadBalancer — `cloudflared` in the `pantry-bot` namespace makes only outbound connections to Cloudflare's edge and forwards to pantry-bot's in-cluster `ClusterIP` Service. **Manual step required at cutover, not automatic**: the tunnel's ingress rule (configured in Cloudflare's dashboard, outside this cluster) currently points at the old Docker service name `http://bot:3000` and must be changed to `http://pantry-bot.pantry-bot.svc:3000`.
- **Everything else is `ClusterIP`-only**, reachable in-cluster via standard k8s DNS: `<service>.<namespace>.svc` (or `.svc.cluster.local`).

### Externally-reachable ports (real production ports, post-cutover)

| Port | Protocol | Service | Reaches |
|---|---|---|---|
| `25565` | TCP | `minecraft.minecraft` (LoadBalancer) | Minecraft, via router port-forward |
| `3001` | TCP | `uptime-kuma.observability` (LoadBalancer) | Uptime Kuma dashboard |
| `3002` | TCP | `grafana.observability` (LoadBalancer) | Grafana dashboard |
| (Cloudflare Tunnel, no port) | — | `pantry-bot.pantry-bot` (ClusterIP, via `cloudflared`) | pantry-bot's HTTP/WS API |

**Important — these three LoadBalancer ports will show `EXTERNAL-IP <pending>` and their `svclb-*` pod will CrashLoopBackOff on first apply**, because Docker Desktop's containers are still holding `3001`/`3002`/`25565` on the host. This is expected, documented per-manifest, and resolved by the two-pass / verify-then-cutover pattern described in section 6. Don't debug it as a fault.

**Confirmed for `observability`, live**: Grafana (`3002`) and Uptime Kuma (`3001`) both bound `EXTERNAL-IP 192.168.40.208` cleanly on first apply (`kubectl -n observability get svc`) — the old Docker containers for both were stopped *before* the Services were applied, so ServiceLB never hit the port conflict above and the delete+reapply workaround this section anticipated wasn't needed in practice. Minecraft's `25565` is still untested — `msh.service` is still live and holding that port as of this writing.

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

### `jmusicbot` namespace

**Live status: fully migrated and verified, as of this writing.** Both Deployments `1/1 Running`. `jmusicbot`'s logs confirm `serversettings.json loaded`, `YouTube access token refreshed successfully`, `Login Successful!`, `Finished Loading!` — the PVC data migration and the OAuth token both survived, no Discord re-auth was needed. `jmusicbot-release-notifier`'s log shows `Last seen release: v0.7.0`, matching `last_release.json` on the pre-migration host path — its state survived too. Old Docker containers for this stack are stopped and no longer present in `docker ps`.

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `jmusicbot` (Deployment, Recreate) | `jmusicbot-custom:yts1182` — **locally built, side-loaded, never pulled from a registry**. `imagePullPolicy: IfNotPresent` is mandatory; `Always` breaks it. | none (outbound Discord gateway only) | 500m/2000m CPU, 512Mi/1Gi mem | `10001:10001` (verified via `docker top`) | `jmusicbot-sa`, no API access | `jmusicbot-config` (1Gi) |
| `jmusicbot-release-notifier` (Deployment, Recreate) | `ghcr.io/chayzx/jmusicbot-release-notifier:latest` (public image, no pull secret needed) | none | 50m/100m CPU, 64Mi/128Mi mem | root (image default, not forced) | `jmusicbot-release-notifier-sa`, no API access | `jmusicbot-notifier-data` (256Mi) |

**jmusicbot is deliberately NOT managed by Keel.** It has its own rebuild pipeline (`scripts/auto-update.sh`, cron-driven, daily) that builds a uniquely-tagged local image each run and deploys via `docker save ... | sudo k3s ctr images import -` then `kubectl set image` — never `rollout restart`, since the image *reference* itself must change to trigger a rollout.

### `pantry-bot` namespace

**Live status: applied and live, cut over 2026-08-11.** All 6 manifests applied, both Deployments `1/1 Running`, real production data migrated (zero-downtime `better-sqlite3` backup from the live Docker container), Cloudflare Tunnel fully repointed at the k8s Service — see the top status section for the full incident/fix narrative. Old Docker containers `pantry-bot-bot-1` and `pantry-bot-watchtower-1` are still running but no longer receive traffic — left up deliberately as a fallback during the soak period. `pantry-bot-cloudflared-1` has been stopped (no longer needed once the k8s `cloudflared` connector was verified serving all four tunnel hostnames).

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `pantry-bot` (Deployment, Recreate) | `ghcr.io/chayzx/pantry-bot:latest` (**private**, needs `imagePullSecrets: ghcr-pull-secret`) | 3000 (http), 8080 (ws) | 100m/250m CPU, 128Mi/256Mi mem | `1000:1000` (verified) | `pantry-bot-sa`, no API access | `pantry-bot-data` (1Gi, holds `pantry.db` SQLite + WAL) |
| `cloudflared` (Deployment, RollingUpdate — safe here, stateless) | `cloudflare/cloudflared:latest` | 2000 (metrics/`/ready`) | 50m/200m CPU, 64Mi/128Mi mem | `65532:65532` (nonroot, verified) | `cloudflared-sa`, no API access | none |

**`pantry-bot` carries Keel annotations** (`keel.sh/policy: force`, `keel.sh/trigger: poll`, `keel.sh/pollSchedule: "@every 5m"`) reproducing the exact Watchtower behavior it replaces: poll GHCR every 5 minutes, redeploy on digest change even though the tag (`:latest`) never changes. **Two separate credentials are involved and both are required**: `imagePullSecrets` (kubelet pulls the image) and Keel's own registry credentials (Keel queries the GHCR API for the current digest) — missing the latter fails *silently*, the pod runs fine and Keel just never updates it.

**Known probe hazard, already fixed in the manifest — don't "simplify" it**: pantry-bot's `/` returns HTTP 404 (verified live through the tunnel), which a plain `httpGet` probe would treat as failure on every check, permanently CrashLooping a healthy app. The probes use the same `node -e "require('http').get(...)"` exec-based check the original Compose healthcheck used (any response = alive), not `httpGet`. If a `/healthz` route returning 200 is ever added to the app, switch to `httpGet` then — not before.

### `observability` namespace

**Live status, as of this writing**: all six workloads — `loki`, `prometheus`, `grafana`, `uptime-kuma`, `promtail` (DaemonSet), `kube-state-metrics` — are `Running`/`1/1` and healthy. The old Docker promtail container is deliberately still running alongside the new one — it's still the one feeding Loki with Minecraft's host logs until Minecraft migrates; don't stop it early.

| Workload | Image (pinned) | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `loki` (Deployment, Recreate) | `grafana/loki:3.3.2` | 3100 (http), 9095 (grpc) | 50m/500m CPU, 128Mi/512Mi mem | `10001:10001` | `loki-sa`, no | `loki-data` (20Gi) |
| `prometheus` (Deployment, Recreate) | `prom/prometheus:v3.1.0` | 9090 | 100m/1000m CPU, 512Mi/1Gi mem | `65534:65534` (nobody) | `prometheus-sa`, **yes** (`kubernetes_sd_configs`, scrapes kubelet/cAdvisor) | `prometheus-data` (20Gi) |
| `grafana` (Deployment, Recreate) | `grafana/grafana:11.4.0` | 3000→3002 externally | 50m/500m CPU, 128Mi/512Mi mem | `472:472` | `grafana-sa`, no | `grafana-data` (2Gi) |
| `uptime-kuma` (Deployment, Recreate) | `louislam/uptime-kuma:2` | 3001 | 50m/500m CPU, 192Mi/768Mi mem | `0:0` (root — upstream-mandated, drops privileges internally; forcing non-root breaks startup) | `uptime-kuma-sa`, no | `uptime-kuma-data` (2Gi, includes embedded MariaDB datadir) |
| `promtail` (**DaemonSet**, not Deployment — log shipper needs one pod per node) | `grafana/promtail:3.3.2` | 9080 | 50m/200m CPU, 64Mi/256Mi mem | `0:0` (root, required — container logs under `/var/log/pods` are root-owned) | `promtail-sa`, **yes** (`kubernetes_sd_configs`, role: pod) | none (hostPath positions file at `/var/lib/promtail` instead) |
| `kube-state-metrics` (Deployment, RollingUpdate — stateless) | `registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.13.0` | 8080, 8081 | 10m/100m CPU, 32Mi/128Mi mem | `65534:65534` | `kube-state-metrics-sa`, **yes** (lists/watches nearly every object type, read-only) | none |

**`uptime-kuma` — resolved. Took three rounds to fully nail down, all stemming from the same root cause**: `capabilities.drop: ["ALL"]` genuinely strips a UID-0 process of nearly everything that makes it behave like unconfined root. Full chain, in the order each was found:
1. `CAP_CHOWN`/`CAP_FOWNER`/`CAP_SETUID`/`CAP_SETGID` — needed for the outer Node.js process's own `chown`/`chmod` on the MariaDB datadir, and for `mariadbd`'s internal privilege-drop to UID 1000 (`--user=node`). Sourced from the actual uptime-kuma and MariaDB code, not guessed — see the manifest's own header comment for exact file/line citations.
2. A stale root-owned `mysqld.pid` left over from an earlier crash attempt (before fix #1 landed) sat inside an otherwise-correctly-1000:1000-owned `/app/data/run/` — cleared via a temporary debug pod, not a manifest change (it was leftover live data, not a config problem).
3. `CAP_DAC_OVERRIDE` — the piece the original analysis explicitly (and reasonably, for what it analyzed) left out. It correctly reasoned `mariadbd`'s own bootstrap never needs it, but missed that the *outer* Node.js process — which stays root throughout and never drops privilege, unlike `mariadbd` — separately connects to `/app/data/run/mariadb.sock` as a MySQL client. That socket is owned `1000:1000`, root isn't a member of group 1000 here, and "other" permissions are `r-x` (no write) — so a capability-stripped root process fails `connect()` with `EACCES` exactly like a normal non-root user would. Real/unconfined root only bypasses this via `CAP_DAC_OVERRIDE`.

Final capability set: `add: ["CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE"]`. Confirmed healthy end-to-end: pod `1/1 Running`, 0 restarts, `http://192.168.40.208:3001` returns a real response, and its pre-migration monitor configuration survived (existing "Discord Music Bot"/"Twitch Bot" monitors are visible and correctly show the already-documented `docker.sock`-unavailable warning — a known, separate follow-up item, not a new bug).

**Dropped from the old Compose stack, deliberately, not an oversight**: standalone `cadvisor` (kubelet exposes the same data natively at `/metrics/cadvisor` — Prometheus scrapes that instead; also removes the one `privileged: true` container from the whole stack) and a `node-exporter` DaemonSet (a **native host `node_exporter` already runs on `:9100`** — a DaemonSet would collide with it; Prometheus scrapes the existing binary as a static target at `192.168.40.208:9100` instead).

**HDD-specific tuning, present because `/` is spinning rust, not an SSD**: Prometheus retention shortened to 10d/15GB and `--storage.tsdb.max-block-duration=6h` (small frequent compactions instead of rare huge ones that would stall alongside Minecraft's chunk I/O); every stateful store here gets a generous `startupProbe` (Loki: 10 min, Prometheus: 15 min, Uptime Kuma: 10 min) so WAL replay / MariaDB recovery after an unclean stop has time to finish before liveness starts killing the pod.

**Log retention, audited and fixed during cutover**: Loki's `limits_config.retention_period` is `720h` (30d), enforced by `compactor.retention_enabled: true` — both keys are required together or nothing is actually deleted. Loki is the durable, retention-bounded long-term copy; pod stdout on disk is separately bounded by kubelet's own rotation (`containerLogMaxSize`/`containerLogMaxFiles`, currently upstream defaults of 10Mi×5 — a tightened `container-log-max-files=3`/`container-log-max-size=10Mi` is researched and documented in `_bootstrap/LOG-ROTATION.md` but **not yet applied live**, since it needs a deliberate `sudo systemctl restart k3s`, not a side effect of an unrelated change). Minecraft's container image uses a **stdout-only** `log4j2.xml` (Paper's own bundled config with just the file-rolling appender removed, verified byte-for-byte against the live jar) rather than also writing an unbounded `logs/latest.log` to the world PVC — confirmed live that the bare-metal setup had 102 rotated `.log.gz` files going back to March with zero pruning, which would have been genuine unnecessary duplication once Loki is the durable copy anyway.

**promtail carries two hostPath mounts, both required**: `/var/log/pods` (standard k8s container logs) *and* `/home/chase/minecraft/logs` (the still-bare-metal Minecraft server, which doesn't move into the cluster until Phase 4 of the migration — removing this mount early blackholes Minecraft's logs for the whole migration window).

`observability/README.md` has the full apply order, the verification-only-file list, the 3001/3002 cutover sequence, and all four `chown` command blocks paired with their `cp -a`/PV-path-lookup steps.

**ConfigMap ownership, resolved**: `observability/grafana-provisioning.yaml` is authoritative for the two provisioning ConfigMaps (`grafana-provisioning-datasources`, `grafana-provisioning-dashboards`) — these are what `grafana.yaml`'s volume mounts reference. Dashboard *content* is a separate ConfigMap, contractually named `grafana-dashboards`, owned by whatever lands in `dashboards/dashboards-configmap.yaml` `[PENDING]`. Until that lands, `grafana-provisioning.yaml` ships a placeholder empty `grafana-dashboards` ConfigMap so `kubectl apply -f observability/` produces a working (if dashboard-less) Grafana rather than a broken mount — the README has the exact replacement command for swapping the placeholder for the real one.

### `minecraft` namespace

**Live status: not started.** No `minecraft` namespace exists yet (it's created by `minecraft/minecraft.yaml` itself, not `_bootstrap` — see section 2's note on why). `msh.service` is still `active` on the host and still holds port 25565 (`ss -tlnp` confirms). Nothing in this section has been applied.

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `minecraft` (Deployment, Recreate) | `localhost/paper-minecraft:1.21.11-b127` — locally built, side-loaded via `docker save ... \| sudo k3s ctr images import -`, never pulled | 25566/tcp (game), 25566/udp (query), 25575/tcp (rcon) | **requests 2 CPU/3Gi mem, limits 6 CPU/8Gi mem** | `1000:1000` | `minecraft-sa`, no API access | `minecraft-world` (10Gi) |

**Heap: `-Xms2G -Xmx6G`** (down from the pre-migration `-Xmx12G` — user decision, the single change that made the cluster-wide memory budget feasible) plus explicit `-XX:MaxDirectMemorySize=1G -XX:MaxMetaspaceSize=512m` so Netty's direct-buffer ceiling (which otherwise defaults to ≈Xmx) can't silently eat the rest of the 8Gi limit. **Do not tighten the 8Gi limit below the heap** — pods cannot swap under default kubelet cgroup v2 settings, so exceeding the limit is an instant, unrecoverable cgroup OOM-kill with zero GC opportunity and zero world save.

**No liveness probe, by design.** Only a `readinessProbe` (`tcpSocket: 25566`, generous timing — up to ~5.5 min startup grace for a 1.3GB world loading off a 5900rpm HDD from a cold page cache). A Paper server that's laggy under a world-gen burst or a chunk-save spike is *alive* and will recover; a livenessProbe would SIGKILL it mid-tick and risk the world with it.

**`terminationGracePeriodSeconds: 120`** (not the 30s default) — not enough time to flush a 1.3GB world on a spinning disk otherwise, which risks corruption. Shutdown path: `preStop` runs an RCON `save-all flush` + `stop` via a small helper JVM (`rcon.jar`, `-Xmx32m`), falling through non-fatally to `SIGTERM` → the JVM's own shutdown hook (java is PID 1, verified) if RCON fails for any reason.

**Two-pass apply, controlled by the label `homelab.chase/cutover-stage`**: everything except the production `minecraft` LoadBalancer Service is labeled `pre` and safe to apply anytime; the LoadBalancer Service alone is labeled `cutover` and **must not be applied while `msh.service` still owns port 25565 on the host** — doing so makes ServiceLB install a hostPort DNAT rule that hijacks live player traffic into a pod that may not even be ready yet. See `minecraft/MIGRATION.md` for the exact sequencing (written and present in the repo, not pending).

`minecraft-rcon` is a `ClusterIP` Service (never external — RCON is a plaintext, password-gated admin channel) reached at `minecraft-rcon.minecraft.svc.cluster.local:25575` by `scripts/minecraft_backup.py` and `scripts/minecraft_exporter.py`.

`minecraft/MIGRATION.md` is the full 10-step serial cutover runbook — both corrections are folded in (no playit.gg, router port-forward instead; `msh.service` must stop before the NodePort isolation test, not just before the final port flip, since the node can't hold both the old 12G-heap JVM and the new pod's memory at once). Highlights worth knowing without re-reading the whole thing:
- **The build tag `1.21.11-b127` is pinned to the exact live Paper build** (`version_history.json`: `1.21.11-127-bd74bf6`) — a future Paper upgrade is a deliberate, reviewable tag bump, not a silent `:latest` pull.
- **`entrypoint.sh` refuses to start against an empty/unmounted data dir** — without this guard, a botched PVC mount would let Paper silently generate a brand-new world and pass its readiness probe, looking like a successful migration while players spawn into nothing and the real world sits untouched on the old disk.
- **Rollback has three distinct cases** depending on whether a player has joined the new pod yet (pre-flip: free; post-flip-no-player: free; post-player-join: restarting `msh` **silently reverts to the frozen old world with zero error message** — the runbook's recovery path is to promote the PVC's current state back to bare-metal instead of reverting, so nothing played is lost).
- `playit.service` gets disabled as the very last cleanup step, only after the new Service is confirmed bound and joinable — not before.

### `keel` namespace

**Live status: applied and live, cut over 2026-08-11.** Deployment `1/1 Running`, zero errors. `keel-registry-creds` was created by reusing `ghcr-pull-secret`'s existing PAT (same credential is valid for both consumers per this file's own guidance above — no new secret was minted). Live testing found the RBAC comment below was wrong about ungranted resource kinds being silently ignored; see the top status section for the fix (read-only grants added for statefulsets/daemonsets/cronjobs, comment corrected in the manifest itself).

| Workload | Image | Ports | Requests/Limits | UID | SA (API access?) | PVC |
|---|---|---|---|---|---|---|
| `keel` (Deployment, Recreate) | `ghcr.io/keel-hq/keel@sha256:b76f24cc17a69ba4b0c15f81641fa4bfa18b7400e6ae416e478fcdd86ae46569` — pinned by **digest**, not tag; see below for why | 9300 (UI, unused by this design — no Service exposes it) | 50m/100m CPU, 64Mi/128Mi mem | `666:666` (image's own built-in non-root user, verified via `docker image inspect`) | `keel-sa`, **yes**, cluster-scoped (the one workload that needs it) | none (emptyDir for its internal sqlite state — not worth persisting, Keel rebuilds its view from the API) |

**Why a digest pin instead of the tagged `0.21.1` release**: verified directly against `keel-hq/keel`'s commit log that a real fix for polling *mutable* tags (`fix(poll): seed digest watcher from the running image digest`, commit `2f245c5`, 2026-08-10) landed on `master` **after** the 0.21.1 release — and `:latest` (what pantry-bot uses) is exactly a mutable tag. Since no newer versioned release exists yet, the `master` floating tag was resolved to a specific digest confirmed (via the image's own `org.opencontainers.image.revision` OCI label) to be built from a commit *after* the fix. This is recorded as a deliberate tradeoff, not a clean answer — `master` is unversioned CI output with no changelog guarantee, and a digest pin means Keel won't self-update; it needs manual re-pinning periodically, or a switch back to a real version tag once one ships. Re-pin instructions are in the manifest's own header comment.

**RBAC** (`keel/20-clusterrole-clusterrolebinding.yaml`) is cluster-scoped by necessity (the Deployment it watches, `pantry-bot`, lives in a different namespace) but deliberately trimmed from keel-hq/keel's own upstream Helm chart default — no blanket `delete`, no StatefulSet/DaemonSet/Job access this home-lab doesn't use. Extend it only if a future app is put under Keel's management with a workload kind not already covered.

**Two separate credential paths, explicit not automatic**: Keel uses its own `DOCKER_REGISTRY_CFG` env var (from a `keel-registry-creds` Secret, see `keel/SECRETS.md`) to poll the GHCR API, rather than relying on its automatic `imagePullSecrets`-discovery path — the automatic path depends on internal pod-selector matching and fails *silently* (no update, no error) if it ever breaks.

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
| `pantry-bot-data` | `pantry-bot` | 1Gi | `1000:1000` |
| `loki-data` | `observability` | 20Gi | `10001:10001` |
| `prometheus-data` | `observability` | 20Gi | `65534:65534` |
| `grafana-data` | `observability` | 2Gi | `472:472` |
| `uptime-kuma-data` | `observability` | 2Gi | `0:0` |
| `minecraft-world` | `minecraft` | 10Gi | `1000:1000` |

**Every one of these UID/GID pairs was verified against the actual running container** (`docker top <container> -o user,group` or equivalent) during manifest-writing, not assumed from the base image — this mattered because the pre-migration data on disk is uniformly owned `1000:1000` (Docker Desktop's virtiofs fakes ownership on bind mounts; bare-metal k3s enforces real UIDs), so several of these need an explicit `chown` after the data copy, or the pod crash-loops on `EACCES`. Each manifest carries the exact `chown` command in its own header comment.

## 6. RBAC model

- **Every workload gets its own dedicated ServiceAccount**, named `<app>-sa`, namespaced with the app. `automountServiceAccountToken: false` is set explicitly wherever the app has no legitimate reason to call the Kubernetes API — which is most of them. Only four workloads actually need API access, and each has a narrow, read-only (or near-read-only) Role for exactly what it does: `prometheus-sa` (service discovery + kubelet/cAdvisor scraping), `promtail-sa` (pod discovery for log labeling), `kube-state-metrics-sa` (broad read-only listing — that's its whole job), and (once written) `keel`'s SA (watch/patch Deployments cluster-wide).
- **`homelab-admin`** is a `ClusterRole` (`_bootstrap/10-homelab-admin-clusterrole.yaml`) bound to a *named human* kubectl identity, not to any group every pod lands in. It's the union of everything needed to manage this home-lab day to day: full CRUD on ServiceAccounts/Services/ConfigMaps/Secrets/PVCs/Deployments/Roles/RoleBindings, plus read access and pod delete/exec/port-forward for debugging. Its own header comment records the honest caveat: this combination is *functionally* equivalent to cluster-admin (create-Deployment + create-ServiceAccount + read-Secret lets the holder mount any token in the cluster), so it's a convenience/audit-trail boundary for a trusted operator, not a security boundary against a hostile one.
- **Explicitly rejected, and recorded as a deliberate decision** (not an oversight to "fix" later): granting `system:authenticated` — or any group every pod's ServiceAccount lands in by default — edit rights on ServiceAccounts, Roles, RoleBindings, or Secrets. That's a textbook pod-to-cluster privilege-escalation path (a compromised pod could mint or repoint a ServiceAccount and inherit its permissions). The user was asked directly and chose against it.

## 7. Cross-cutting patterns worth knowing before touching anything

- **`imagePullPolicy` is load-bearing, not a style choice, on every locally-built image** (`jmusicbot`, `minecraft`). It must be `IfNotPresent` — these images are side-loaded via `docker save <tag> | sudo k3s ctr images import -` and exist in **no registry**; `Always` (or the implicit default on a `:latest` tag) sends the kubelet looking for a registry that doesn't have them, and the pod sits in `ErrImagePull` forever.
- **`fsGroupChangePolicy: OnRootMismatch`** appears on every PVC-backed pod's `securityContext`, deliberately not left at the `Always` default — `Always` recursively `chown`s the *entire volume* on every single pod start, which on the HDD means minutes of I/O contention with Minecraft before the app even starts. `OnRootMismatch` checks only the volume root and skips the walk once ownership is already correct.
- **Startup probes are generous everywhere data has to load off the HDD** (Loki WAL replay, Prometheus WAL replay, Uptime Kuma's MariaDB recovery, Minecraft's world load) — the pattern is a long `startupProbe` gating a much stricter `readinessProbe`/`livenessProbe`, so a slow-but-healthy cold start can't be mistaken for a crash loop.
- **Config-revision annotations** (`homelab/config-revision` or `homelab.local/config-revision`) appear on several pod templates specifically because editing a ConfigMap does **not** automatically restart the pods consuming it — bumping this annotation (or `kubectl rollout restart`) is the documented way to pick up a config change.

## 7a. Dashboards (`dashboards/`)

Four dashboards, all built, embedded in the `grafana-dashboards` ConfigMap (`dashboards/dashboards-configmap.yaml`, ~151KiB on disk as of this re-verification — grown a bit since it was last measured, still well under the 1MiB ConfigMap ceiling; confirmed live via `kubectl -n observability get configmap grafana-dashboards`). All use `${DS_PROMETHEUS}`/`${DS_LOKI}` datasource variables rather than hardcoded UIDs, so they import cleanly regardless of what UID a given Grafana instance assigns.

| Dashboard | Covers |
|---|---|
| `cluster-overview.json` | Node-level health from the native `node_exporter` (CPU/mem/disk/load), explicit HDD I/O latency/utilization panels (root is a 5900rpm disk — Prometheus compaction, Loki flush, and Minecraft saves all contend for it), k3s Node-Ready status, memory breakdown with reference lines at total capacity (~15Gi) and the `system-reserved=3Gi` floor |
| `pods-and-workloads.json` | Per-namespace/per-pod usage **plotted against configured limits** (the plan's specific ask) — memory and CPU, both as absolute usage and %-of-limit bar gauges (red above 85%), restart counts, OOMKill events (cross-checked against two independent metrics since `container_oom_events_total` is known-unreliable on some runtimes), Deployment replica health. One flagged, self-resolving TODO: a PVC-usage panel needs `kubelet_volume_stats_*` from the kubelet's main `/metrics` endpoint — confirmed present via the `kubernetes-kubelet` scrape job in `prometheus-config.yaml` (added independently by the observability agent), so this isn't actually a gap. |
| `logs.json` | **The severity-filtering deliverable.** A `level` template variable (multi-select debug/info/warn/error) wired directly into every LogQL stream selector, so filtering happens at Loki's index/chunk level, not client-side. Plus namespace/pod drill-down, free-text search, a log-volume-by-severity graph, an always-on error count/panel that ignores the level filter (so "how many errors right now" never depends on a dropdown state), and an "unclassified lines" counter that catches any app whose promtail parsing stage breaks or was never written. |
| `apps.json` | Minecraft (real RCON-based metrics: TPS, players, world size, server-up; memory via cAdvisor plotted against both the 8Gi limit and the 6Gi heap ceiling so JVM overhead is visually distinct from real growth), jmusicbot and pantry-bot (both honestly limited to pod-status/restarts/memory-vs-limit — neither has real app metrics; pantry-bot's "health" panel is `kube_pod_status_ready`, not real HTTP health, with the panel description saying so explicitly rather than inventing a metric). |

**How the severity filter actually works, mechanically**: promtail's `pipeline_stages` (`observability/promtail-config.yaml`) extract a `level` value per line via regex/JSON and promote it to a real Loki **label** via a `labels:` stage — not just a parsed field. That's what makes `{namespace=~"$namespace", level=~"$level"}` work as a stream selector Loki resolves before reading log bodies, rather than a slow client-side text scan. If a given app's parsing stage ever breaks, its logs don't vanish — they fall into "unclassified" (a dedicated stat panel exists specifically to catch that).

## 8. Deliberately not deployed

Not a gap — a documented decision. `_bootstrap/WATCHERS-TODO.md` covers `observability-watcher` and `observability-network-exporter`, both bind-mounting `/var/run/docker.sock` today, which **does not exist at all** under k3s's containerd-only runtime (not "moved," not "needs a different path" — gone). Neither is included in any manifest in this repo, and neither should be deployed as-is: their behavior against a missing socket is unverified (their source is private and wasn't read), and both are plain Python scripts that would need a genuine rewrite against the Kubernetes API, not a config change.

- `observability-watcher` (container down/restart-loop alerts) → replace with Alertmanager rules on `kube_pod_status_phase`/`kube_pod_container_status_restarts_total` (from kube-state-metrics, already deployed), routed to the same Discord webhook via Alertmanager's native Discord receiver.
- `observability-network-exporter` → likely **fully redundant**, not just unported. It exists specifically to work around cAdvisor's broken per-container network metrics *under Docker Desktop's VM networking* (`pid: host`, misreported host-tunnel interfaces). Bare-metal k3s pods have real veth interfaces on a real netns — the kubelet's built-in `/metrics/cadvisor` almost certainly already reports correct per-pod network I/O with no separate exporter needed. Verify this before writing a single line of a port.
- **Migration-window hazard, act before Phase 2**: `observability-watcher` watches Docker container *names* (`WATCH_CONTAINERS=jmusicbot,pantry-bot-bot-1`). The moment either is stopped during cutover, it'll fire a false "container down" Discord alert for an app that's actually fine, just relocated. Blank `WATCH_CONTAINERS` and pause the equivalent Uptime Kuma monitors before touching jmusicbot or pantry-bot.

## 9. Status: live cutover in progress, applied namespace-by-namespace

Every namespace's manifests, the dashboards, the RBAC model, and the script rewrites are written and reviewed (repo `~/k8s-homelab/`, git history, oldest to newest: `44f1db9` initial manifest set → `48e09b9` fix an invalid ConfigMap label → `3804dc5` fix three live cutover bugs + log-retention audit → `ad0c0f6` confirm JVM_OPTS/rsync for stdout-only logging → `5dc0e4b` fix uptime-kuma chown instructions (mariadb/ subdirectory needs its own 1000:1000 chown, not just the whole-PVC one) → `f041581` uptime-kuma: add `CAP_DAC_OVERRIDE`, the fifth and final capability needed → `a49626c` ARCHITECTURE.md: mark uptime-kuma fully resolved. Seven commits total; `git status` is clean — nothing uncommitted. Cutover is being executed by hand per each namespace's `README.md`/`MIGRATION.md`, in the order: Phase 0 remainder (secrets, storage decisions) → observability → jmusicbot → pantry-bot + Keel → Minecraft (highest risk, most novel, done last) → Docker Desktop retired only after a real soak period.

**Actual progress as of this writing** (freshly re-verified live against the running cluster, not transcribed from a plan or an earlier doc pass): `_bootstrap` done. `observability` fully applied and healthy, all six workloads `1/1 Running` including uptime-kuma (confirmed via `kubectl -n observability get pods`, `kubectl -n observability logs`, and `curl` against both `http://192.168.40.208:3001` → `302` (uptime-kuma) and `http://192.168.40.208:3002/api/health` → `200` (grafana) — note the port pairing is the reverse of an earlier draft of this line; verified directly by content, not assumed from port order). `jmusicbot` fully migrated and verified (both Deployments `1/1 Running`, logs confirm OAuth token and state survived). `pantry-bot` and `keel` are now also applied and live — both Deployments `1/1 Running`, pantry-bot's production data migrated with zero downtime, its Cloudflare Tunnel fully repointed at k8s targets (see top status section for the full incident/fix narrative). Only `minecraft` remains not started — `kubectl get ns` shows no `minecraft` namespace yet, and `msh.service` (still bound to `25565` per `ss -tlnp`) remains the live production path. `playit.service` is still `active`/`enabled` on the host, unresolved and flagged, not a blocker.

Once the remaining namespaces land, update this document — it is meant to stay current, not describe a snapshot.
