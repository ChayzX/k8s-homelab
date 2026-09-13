# `jmusicbot` namespace

Migrates `/home/chase/docker/jmusicbot/docker-compose.yml` (2 services) to k3s.

| Compose service    | k8s object                              | State |
|--------------------|-----------------------------------------|-------|
| `jmusicbot`        | Deployment `jmusicbot`                  | `emptyDir` restored/synced through R2 at `/musicbot` |
| `jmusicbot` (health) | Service `jmusicbot-health`            | ClusterIP :9091, in-cluster only — Uptime Kuma + probe target |
| `release-notifier` | Deployment `jmusicbot-release-notifier` | PVC `jmusicbot-notifier-data` (256Mi) at `/data` |

Only the health endpoint (see "Design notes") is exposed, and only in-cluster
via the ClusterIP Service; neither workload talks to the Kubernetes API.

---

## Apply order

```bash
# 0. namespace (works in an independent cluster too)
kubectl apply -f 00-namespace.yaml

# 1. secrets (imperative, never in git) — see SECRETS.md
#    creates: jmusicbot-config-txt, jmusicbot-notifier-secrets

# 2. identity + notifier config + optional notifier storage
kubectl apply -f 10-serviceaccounts.yaml
kubectl apply -f 20-configmap.yaml
kubectl apply -f 30-pvcs.yaml

# 3. workloads — notifier FIRST (per the migration plan, Phase 2)
kubectl apply -f 50-deployment-release-notifier.yaml
kubectl apply -f 40-deployment-jmusicbot.yaml
kubectl apply -f 45-service-health.yaml   # health endpoint for probes + Kuma
```

Or, once the secrets exist: `kubectl apply -f .` (filenames are ordered, but
`kubectl apply -f <dir>` sorts alphabetically and applies everything in one
pass — ordering only matters for the Secrets, which are not in this directory).

---

## Manual steps — these are not optional

### A. Side-load the jmusicbot image into containerd

`jmusicbot-custom:yts1182` exists only in the local Docker daemon. k3s uses its
own embedded containerd and cannot see it.

```bash
docker save jmusicbot-custom:yts1182 | sudo k3s ctr images import -
sudo k3s ctr images ls | grep jmusicbot-custom     # confirm before applying
```

The Deployment sets `imagePullPolicy: IfNotPresent`. **Do not change this to
`Always`** — there is no registry copy to pull and the pod will never start.

### B. Restore mutable state from R2

The main bot no longer uses the old `jmusicbot-config` PVC. Its `emptyDir` is
seeded by the `restore-state` init container and synchronized by the `r2-sync`
sidecar under `r2:pantry-bot-backups/jmusicbot/`. This makes the main bot
portable between nodes and independent clusters while retaining one-writer
semantics through `Recreate` and the migration runbook's fencing step.

Before starting a new environment, verify that `jmusicbot-r2` exists, that the
R2 prefix contains a retained generation, and that only one JMusicBot writer is
running. Do not copy state files from a live pod or place R2 credentials in the
repository. The first Oracle cutover procedure is documented in
`docs/recovery/ORACLE-JMUSICBOT-MIGRATION.md`.

Before applying either environment, run the repository-only R2 contract
preflight against the rendered Deployment:

```bash
kubectl kustomize jmusicbot | scripts/jmusicbot-r2-contract-check.sh
```

This does not contact R2 or start a workload. It verifies the approved state
prefix, restore and ownership-gated sync commands, five-minute sync interval,
single-writer `Recreate` strategy, and exclusions that prevent `config.txt`,
JMusicBot backup churn, or the lease marker from being synchronized as mutable
state. A failed preflight means the rendered manifest is not eligible for a
handoff rehearsal.

### C. Deployment authority

The active deployment authority is `.github/workflows/jmusicbot-deploy.yml`.
It publishes the immutable multi-architecture GHCR image, applies the checked-in
manifest through the protected CI tunnel, waits for rollout and health, and
rolls back to the previous image when the health gate fails. The retired local
Docker updater is historical only; do not reintroduce a second writer or a
local image authority.

Editing `auto-update.sh` is outside this directory's scope — it is listed here
so the cutover does not silently keep deploying to a dead Compose stack.

### D. Do not add Keel annotations to `jmusicbot`

Deliberate. It has its own pipeline (see C). Keel is scoped to pantry-bot only.

### E. Health endpoint — build + deploy the `-health1` image

The Deployment's probes and the `jmusicbot-health` Service target port **9091**,
which only the health-patched image listens on. A stock
image has no listener there and the pod would sit NotReady forever, so build the
patched image **before** applying the manifest.

```bash
# Option 1 — production path: auto-update.sh re-clones upstream MusicBot,
# applies scripts/patches/jmusicbot-health-endpoint.patch (plus the voice-chat
# patch), builds, imports into containerd, and `kubectl set image`s the new tag.
# The build tree's state file lacks the "health1" marker, so one run rebuilds now:
cd /home/chase/k8s-homelab/scripts && ./auto-update.sh

# Option 2 — one-time manual build from the already-patched build tree:
cd /home/chase/docker/jmusicbot/custom-build
docker build -t jmusicbot-custom:v0.7.0-yts1.18.2-voicechat1-health1 .

# Then (or auto-update.sh does the first two of these for you):
docker save jmusicbot-custom:v0.7.0-yts1.18.2-voicechat1-health1 | sudo k3s ctr images import -
kubectl -n jmusicbot apply -f 40-deployment-jmusicbot.yaml
kubectl -n jmusicbot apply -f 45-service-health.yaml
kubectl -n jmusicbot rollout status deployment/jmusicbot --timeout=180s
```

Verify from inside the cluster:

```bash
kubectl -n jmusicbot run --rm -it --restart=Never curl-health --image curlimages/curl \
  -- curl -s -o /dev/null -w '%{http_code}\n' \
     http://jmusicbot-health.jmusicbot.svc.cluster.local:9091/health
```

Expect `200` once the bot is logged in (until then it's `503`). Then point the
Uptime Kuma "Discord Music Bot" monitor (k8s-homelab-6ba) at that URL — `200` is
UP, `503` is still starting, connection-refused is DOWN.

### F. YouTube poToken fix — build + deploy the `potok` image (k8s-homelab#41)

Fixes "song cuts off partway / Sign in to confirm you're not a bot" — YouTube
started blocking lavaplayer's `TVHTML5`/`TVHTML5_SIMPLY` clients. The fix sends
a **poToken + visitorData** on the `Web` client, the same mechanism the
SeVile/MusicBot fork exposes via `ytpotoken`/`ytvisitordata` config keys. The
OAuth token (`youtubetoken.txt`, in the R2-backed mutable state) is unaffected and still
required — the poToken is an *additional* anti-bot signal.

- Patch source: `scripts/patches/jmusicbot-potoken.patch` (adds the config keys
  to `ConfigOption`/`BotConfig` and calls `Web.setPoTokenAndVisitorData()` in
  `AudioSource`). The patch is already applied in the `potok` image.
- Config: `config.txt` (Secret `jmusicbot-config-txt`) needs the top-level keys
  `ytpotoken` and `ytvisitordata`; both must be present or playback can worsen.
  Values come from a trusted-session generator (e.g. `youtube-trusted-session-generator`).
- Build (same path as E):
  ```bash
  cd /home/chase/docker/jmusicbot/custom-build
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker build --build-arg BUILD_TIMESTAMP="$TS" -t jmusicbot-custom:potok .
  docker save jmusicbot-custom:potok | sudo k3s ctr images import -
  kubectl -n jmusicbot set image deployment/jmusicbot jmusicbot=docker.io/library/jmusicbot-custom:potok
  kubectl -n jmusicbot rollout status deployment/jmusicbot --timeout=180s
  ```
- Verify: the pod log must contain
  `[INFO] [AudioSource]: Applied YouTube poToken + visitorData (Web client)`.
  A `Deprecated/unknown keys (will be ignored): [ytpotoken, ytvisitordata]`
  warning is cosmetic (ConfigDiagnostics doesn't know the new keys, but the bot
  reads them fine) and is harmless.

---

## Verification

```bash
kubectl -n jmusicbot get pods -o wide
kubectl -n jmusicbot logs deploy/jmusicbot --tail=50
```

`Running` is not sufficient. The real check is functional:

1. The bot appears online in Discord.
2. Play a track and **hear audio** — the whole reason for the custom image is
   the itag-18 playback bug, and only real playback proves it.
3. Play an auto-generated "Topic" channel upload specifically. That is the case
   that broke on stock v0.7.0.
4. `kubectl -n jmusicbot delete pod -l app.kubernetes.io/name=jmusicbot`, then
   confirm per-guild settings (DJ role, default channel) survived — that proves
   `serversettings.json` was synchronized to and restored from R2.

For the notifier: `kubectl -n jmusicbot logs deploy/jmusicbot-release-notifier`
should show a poll cycle within 15 minutes and no webhook 401/404.

---

## Migration-window hazard

`observability-watcher` watches the Docker container named `jmusicbot`. The
moment the Compose stack goes down, it will fire "container down" Discord alerts
for a bot that is fine and merely relocated. Blank its `WATCH_CONTAINERS` and
pause the matching Uptime Kuma monitors **before** starting this phase. See
`../_bootstrap/WATCHERS-TODO.md`.

---

## Design notes / alternatives considered

**Why `config.txt` as a whole-file Secret.** JMusicBot reads one flat HOCON
file with no env-var override path. Templating just the `token` line would
require an initContainer plus `envsubst` plus a ConfigMap holding the other ~80
settings — two sources of truth for one file, and a merge conflict every time
the config schema changes. Mounting the whole file is simpler and the token is
the only sensitive line anyway. See SECRETS.md for the honest limitations.

**If a future JMusicBot needs to *write* `config.txt`.** The current subPath
mount is read-only. Switch to a copy-on-start pattern:

```yaml
      initContainers:
        - name: seed-config
          image: busybox:1.36
          command: ["sh", "-c", "cp -n /src/config.txt /musicbot/config.txt"]
          volumeMounts:
            - {name: config-txt, mountPath: /src, readOnly: true}
            - {name: config, mountPath: /musicbot}
```

with the secret mounted at `/src` instead of over `/musicbot/config.txt`. Note
the trade-off: `cp -n` means the Secret then seeds the file only once and later
Secret edits are ignored until the pod is restarted (the current subPath mount
is snapshotted at pod start).

**Why the health endpoint exists.** jmusicbot exposes no
port and no HTTP listener of its own, so after the k3s migration the Uptime Kuma
"Discord Music Bot" monitor (which polled `/var/run/docker.sock`) had nothing to
check. The patched image runs a JDK-built-in `com.sun.net.httpserver.HttpServer`
— no new Maven dependency; the image's jlink runtime gains the `jdk.httpserver`
module (see the Dockerfile) — on port **9091** inside the JVM:

| Route | Response |
|-------|----------|
| `GET /health` (alias `/healthz`) | `200 {"status":"ok"}` once logged into Discord, else `503 {"status":"starting"}` |
| `GET /live` | `200 {"status":"alive"}` whenever the JVM is alive |

The ready signal is tied to the bot's real lifecycle: the server starts in
`JMusicBot.startBot()` before login, and the flag flips in
`StartupLifecycleListener.onReady()` when the Discord `ReadyEvent` fires.

**Why readiness probes `/health` but liveness probes `/live`.** The readiness
probe must fail while the bot is starting or cannot log in — that is the state
Uptime Kuma should report as down, and it keeps endpoints (and traffic) off a
half-alive pod. Liveness deliberately does *not* hit `/health`: a Discord auth
or connectivity failure would then make the kubelet restart the pod in a loop,
but JMusicBot already retries its own login. Liveness only needs to catch a
wedged/dead process, which `/live` does. This replaces the earlier "no liveness
probe" reasoning — the original reason was simply that there was no endpoint or
socket to target, not a principled choice.

The endpoint is wired into the rebuild pipeline exactly like the voice-chat fix:
`scripts/patches/jmusicbot-health-endpoint.patch`, gate
`HEALTH_ENDPOINT_RELEASED` in `scripts/auto-update.sh`, image suffix `-health1`
(see section E). It is exposed in-cluster via the `jmusicbot-health` ClusterIP
Service, which is what Uptime Kuma and the probes target.

**Why `Recreate` on a stateless-looking notifier.** Its PVC is RWO and holds the
"last announced release" marker. Two overlapping pods during a rolling update
would both read the stale marker and double-post to Discord.
