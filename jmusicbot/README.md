# `jmusicbot` namespace

Migrates `/home/chase/docker/jmusicbot/docker-compose.yml` (2 services) to k3s.

| Compose service    | k8s object                              | State |
|--------------------|-----------------------------------------|-------|
| `jmusicbot`        | Deployment `jmusicbot`                  | PVC `jmusicbot-config` (1Gi) at `/musicbot` |
| `release-notifier` | Deployment `jmusicbot-release-notifier` | PVC `jmusicbot-notifier-data` (256Mi) at `/data` |

Neither workload publishes a port and neither talks to the Kubernetes API.

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

**Why no liveness probe on either pod.** Neither exposes a health endpoint or a
listening socket. A `tcpSocket`/`httpGet` probe would have nothing to target,
and an `exec` probe checking "is the process alive" is redundant — if the
process dies the container exits and the kubelet restarts it. Inventing a probe
here would only create new ways to kill a healthy pod.

**Why `Recreate` on a stateless-looking notifier.** Its PVC is RWO and holds the
"last announced release" marker. Two overlapping pods during a rolling update
would both read the stale marker and double-post to Discord.
