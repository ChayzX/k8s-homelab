# `jmusicbot` namespace

Migrates `/home/chase/docker/jmusicbot/docker-compose.yml` (2 services) to k3s.

| Compose service    | k8s object                              | State |
|--------------------|-----------------------------------------|-------|
| `jmusicbot`        | Deployment `jmusicbot`                  | PVC `jmusicbot-config` (1Gi) at `/musicbot` |
| `jmusicbot` (health) | Service `jmusicbot-health`            | ClusterIP :9091, in-cluster only — Uptime Kuma + probe target |
| `release-notifier` | Deployment `jmusicbot-release-notifier` | PVC `jmusicbot-notifier-data` (256Mi) at `/data` |

Only the health endpoint (see "Design notes") is exposed, and only in-cluster
via the ClusterIP Service; neither workload talks to the Kubernetes API.

---

## Apply order

```bash
# 0. prerequisite — namespaces exist
kubectl apply -f ../_bootstrap/00-namespaces.yaml

# 1. secrets (imperative, never in git) — see SECRETS.md
#    creates: jmusicbot-config-txt, jmusicbot-notifier-secrets

# 2. identity + config + storage
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

### B. Migrate the data, and fix ownership

The migration plan's UID finding applies here directly, and the numbers are
*not* the ones the host filesystem shows.

`/home/chase/docker/jmusicbot/*` is owned `1000:1000` on the host. That is
Docker Desktop's virtiofs faking ownership. The container actually runs as
**UID/GID 10001** (verified: `docker top jmusicbot -o user,group`). On
bare-metal k3s the real UID is enforced.

```bash
# stop the old stack first — do not hot-copy a running bot's state
cd /home/chase/docker/jmusicbot && docker compose down

# start the new Deployment once so local-path provisions the PVC directory,
# then find where it landed
kubectl -n jmusicbot apply -f 30-pvcs.yaml -f 40-deployment-jmusicbot.yaml
kubectl -n jmusicbot get pvc jmusicbot-config -o jsonpath='{.spec.volumeName}'
PVDIR=$(sudo find /var/lib/rancher/k3s/storage -maxdepth 1 -name '*jmusicbot-config*')

kubectl -n jmusicbot scale deployment/jmusicbot --replicas=0   # release the volume

sudo cp -a /home/chase/docker/jmusicbot/serversettings.json \
           /home/chase/docker/jmusicbot/youtubetoken.txt \
           /home/chase/docker/jmusicbot/.slashcommands.hash \
           "$PVDIR"/
sudo chown -R 10001:10001 "$PVDIR"

kubectl -n jmusicbot scale deployment/jmusicbot --replicas=1
```

Copy the **state** files only. Do not copy `config.txt` (it comes from the
Secret and would be shadowed by the mount anyway), `docker-compose.yml`,
`auto-update.sh`, `custom-build/`, `.env`, or `jmusicbot.service`.

`youtubetoken.txt` matters: `config.txt` has `useOAuth = true`, so losing that
file forces a re-authorisation flow that requires reading the pod log for a
device code.

Same for the notifier, which runs as **root**:

```bash
NPVDIR=$(sudo find /var/lib/rancher/k3s/storage -maxdepth 1 -name '*jmusicbot-notifier-data*')
sudo cp -a /home/chase/docker/jmusicbot/release-notifier-data/last_release.json "$NPVDIR"/
sudo chown -R 0:0 "$NPVDIR"
```

If `last_release.json` is lost the notifier re-announces the newest release once
— cosmetic, but avoidable.

### C. Update `auto-update.sh`'s deploy step

The cron rebuild currently ends in a `docker compose up -d`. Under k3s the tail
of that script becomes:

```bash
docker save "jmusicbot-custom:${NEW_TAG}" | sudo k3s ctr images import -
kubectl -n jmusicbot set image deployment/jmusicbot "jmusicbot=jmusicbot-custom:${NEW_TAG}"
kubectl -n jmusicbot rollout status deployment/jmusicbot --timeout=180s
```

`kubectl set image`, **not** `kubectl rollout restart`. Each build produces a
uniquely-tagged image, so the pod spec's image reference has to change for the
new build to be used at all; `rollout restart` would faithfully restart the pod
onto the *old* tag.

Editing `auto-update.sh` is outside this directory's scope — it is listed here
so the cutover does not silently keep deploying to a dead Compose stack.

### D. Do not add Keel annotations to `jmusicbot`

Deliberate. It has its own pipeline (see C). Keel is scoped to pantry-bot only.

### E. Health endpoint — build + deploy the `-health1` image

The Deployment's probes and the `jmusicbot-health` Service target port **9091**,
which only the health-patched image listens on (bd k8s-homelab-aos). A stock
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
   `serversettings.json` really is on the PVC.

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
Secret edits are ignored until you delete the PVC copy.

**Why the health endpoint exists (bd k8s-homelab-aos).** jmusicbot exposes no
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
