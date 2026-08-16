# Operational scripts (k3s versions)

Kubernetes replacements for the three host/Docker scripts. **The originals are
untouched** — these are new files, meant to be swapped in by hand during the
serial cutover.

| New file | Replaces | Runs |
|---|---|---|
| `auto-update.sh` | `~/docker/jmusicbot/auto-update.sh` | user crontab, daily 04:17 |
| `minecraft_backup.py` | `~/docker/observability/monitoring/minecraft-exporter/minecraft_backup.py` | `minecraft-backup.timer` (user), daily 04:00 |
| `minecraft_exporter.py` | `~/docker/observability/monitoring/minecraft-exporter/minecraft_exporter.py` | `minecraft-exporter.service` (user), always on |
| `kuma-host-heartbeat.sh` | new | user crontab, every minute |

**Retired:** `beads-dolt-pull.sh` (5-minute Dolt pull for the beads boards,
removed with the beads migration — issue tracking now lives in GitHub Issues
+ Projects v2, see `../AGENTS.md`). Its cron line is gone; `bd`, scotty, and
the `.beads/` databases are retired.

The workload scripts below are runbooks and are installed manually as part of
the migration.

---

## Host uptime heartbeat for Uptime Kuma

`kuma-host-heartbeat.sh` is the host-side half of bead `k8s-homelab-8nc`'s
long-term uptime scope. It does **not** collect CPU or memory trends; it only
pushes a minute-by-minute "host is alive" signal into Uptime Kuma so Kuma can
keep host uptime history beyond Grafana's 7-day Prometheus retention window.

Pods and services should continue to use ordinary Kuma HTTP/TCP monitors. This
script is specifically for the bare host, because a push delivered from the
host proves the machine itself is up.

### One-time setup

1. In the Uptime Kuma UI at `https://status.greeniespantry.uk`, add a new
   **Push** monitor for the host. Set its heartbeat interval to `60` seconds.
2. Copy only the generated token into a local env file:

   ```sh
   echo 'KUMA_PUSH_TOKEN=<token>' > ~/.config/kuma-heartbeat.env
   chmod 600 ~/.config/kuma-heartbeat.env
   ```

3. Add the host cron entry:

   ```cron
   * * * * * /home/chase/k8s-homelab/scripts/kuma-host-heartbeat.sh >> /home/chase/k8s-homelab/scripts/kuma-heartbeat.log 2>&1
   ```

4. In Kuma, set the monitor interval to **2 minutes** so two missed pushes mark
   the host down.

---

## 1. `auto-update.sh`

Same three-branch logic as before (upstream-fixed → revert; new release →
rebuild; nothing new → exit). Only the deploy step changed.

**What changed**

- **`docker build` stays.** That is the reason native `docker-ce` is retained
  after the migration. No registry is introduced for a single-node homelab.
- **Image side-load.** k3s's containerd cannot see docker's image store, so
  after the build:
  `docker save <tag> | sudo k3s ctr images import -`.
  `ctr` normalises the bare name to `docker.io/library/jmusicbot-custom:<tag>`,
  which is exactly what the kubelet resolves `image: jmusicbot-custom:<tag>` to,
  so the reference matches with no manifest gymnastics.
- **Deploy is `kubectl set image`, never `kubectl rollout restart`.** Each run
  builds a uniquely tagged image, so the pod spec's image *reference* must
  change for a new ReplicaSet to appear. `rollout restart` would redeploy the
  *old* tag and report success — a silent no-op dressed up as a successful
  update, which is the worst available failure mode here.
- **Manifest is rewritten** at `/home/chase/k8s-homelab/jmusicbot/jmusicbot.yaml`
  (same `sed` on the image line the compose file used to get), so git remains
  the record of what is deployed and a later `kubectl apply -f` cannot silently
  roll the bot backwards. The rewrite is now *verified* (`grep` after `sed`) —
  the compose version trusted it blindly, which mattered less because compose
  was also the deploy mechanism.
- **Rollout is verified** with `kubectl rollout status --timeout=300s`. On
  failure: `kubectl rollout undo`, restore the manifest from a backup, **do not
  write the state file** (so the next run retries rather than concluding it
  succeeded), and Discord-notify with the exact `kubectl logs --previous`
  command to run.
- **Pruning now covers both image stores.** Every build leaves a copy in
  containerd as well as docker; on a daily cron, unpruned, that fills
  `/dev/sda2` within months.
- **Local git commit** of just the manifest path (never pushes; disable with
  `AUTO_COMMIT_MANIFEST=0`).
- **Explicit `PATH` and `KUBECONFIG`.** cron provides neither; `kubectl` and
  `k3s` live in `/usr/local/bin`, and k3s is installed with
  `--write-kubeconfig-mode 644` so `/etc/rancher/k3s/k3s.yaml` is readable.

**The self-terminating "upstream fixed it" path**

The original flipped `com.centurylinklabs.watchtower.enable` back to `true`,
because Watchtower was the host-wide auto-updater and the *only* reason
jmusicbot was excluded from it was that a local-only tag cannot be polled.

Watchtower is gone. Keel is the equivalent, and jmusicbot is off it for exactly
the same reason. So the faithful port is: **when we go back to
`ghcr.io/arif-banai/musicbot:latest`, annotate the Deployment for Keel** —
`keel.sh/policy: force`, `keel.sh/trigger: poll`,
`keel.sh/pollSchedule: "@every 5m"`, matching pantry-bot — then `kubectl apply`,
verify the rollout, and self-uninstall the cron job. This preserves the
original's posture (unreviewed auto-deploys of upstream releases: what the bot
had before the patch, and what it is supposed to get back).

The Keel annotations are inserted into the manifest with a small `awk` pass
rather than `yq` or `kubectl patch --local`, because both of those reformat the
file and drop every comment — and that manifest's comments are the only
explanation of why the custom build existed. The edit is validated with
`kubectl apply --dry-run=server`; if validation fails, the manifest is restored
untouched, the change is applied to the live object with
`kubectl set image` + `kubectl annotate`, and the notification says plainly that
the manifest is now out of sync and needs a hand edit. Fail-loud, never
fail-silent.

`ghcr.io/arif-banai/musicbot` is a public package, so Keel needs no extra
registry credentials for it (unlike pantry-bot's private one).

### Required: scoped sudoers entry for `k3s ctr`

The script runs from the *user* crontab, so `sudo` must not prompt. Add a
dedicated file — **not** blanket `NOPASSWD: ALL`:

```
sudo visudo -f /etc/sudoers.d/jmusicbot-k3s-ctr
```
```
chase ALL=(root) NOPASSWD: /usr/local/bin/k3s ctr images *
```
(mode `0440`, `root:root` — `visudo -f` handles that.)

Scoped to `ctr images`, which covers `ls` / `import` / `rm` (everything the
script uses) while withholding `ctr run` / `ctr task`, which would be trivially
root-equivalent. Stated honestly: this still grants the ability to plant an
arbitrary image into containerd, which is a real privilege — just a far smaller
one than full sudo.

Two gotchas:
- `sudo` resolves the command against `secure_path`, so the sudoers line must
  name the **absolute** path to the `k3s` binary. The script warns if `k3s`
  resolves anywhere other than `/usr/local/bin/k3s`.
- The `import` command must stay in the form `k3s ctr images import -` (no flags
  between `ctr` and `images`), or the wildcard stops matching. This is also the
  form Rancher documents for air-gapped installs, and it lands in the `k8s.io`
  containerd namespace, which is the one the kubelet reads.

The script **preflights this before building** (`sudo -n k3s ctr images ls -q`)
and aborts with the exact `visudo` line in both the log and the Discord message.
Failing after a multi-minute maven build would be a waste.

### Manifest requirements (for whoever writes `jmusicbot/jmusicbot.yaml`)

- Container named `jmusicbot` (override with `JMUSICBOT_CONTAINER`).
- `image: jmusicbot-custom:<tag>` on one line — the `sed` anchors on
  `image: jmusicbot-custom:`.
- The `# BEGIN CUSTOM-BUILD-NOTE` / `# END CUSTOM-BUILD-NOTE` comment block
  carried over from the compose file, at 6-space indent (container level). The
  revert path replaces that block wholesale.
- **Do not set `imagePullPolicy: Always`.** The tag exists only in containerd;
  `Always` guarantees `ErrImagePull` on every rollout. Unique non-`:latest` tags
  already default to `IfNotPresent`, so simply omit it. The script warns if it
  finds `Always`.

### Cron change

```cron
# old
17 4 * * * /home/chase/docker/jmusicbot/auto-update.sh >> /home/chase/docker/jmusicbot/auto-update.log 2>&1
# new
17 4 * * * /home/chase/k8s-homelab/scripts/auto-update.sh >> /home/chase/docker/jmusicbot/auto-update.log 2>&1
```

The self-uninstall greps for `auto-update.sh`, so it still finds the line after
the path change.

The build tree, `.env` (webhook URL) and `.last-built-tag` deliberately **stay**
at `/home/chase/docker/jmusicbot/custom-build/`. They are a build context, not
Kubernetes objects, and keeping the state file in place means the first
post-cutover run does not do a pointless rebuild of an image that already
exists. All paths are env-overridable (`JMUSICBOT_DIR`, `JMUSICBOT_MANIFEST`,
`JMUSICBOT_NAMESPACE`, `JMUSICBOT_DEPLOYMENT`, `JMUSICBOT_CONTAINER`,
`JMUSICBOT_ROLLOUT_TIMEOUT`).

---

## 2. `minecraft_backup.py`

**What changed**

- **RCON through the cluster, two paths tried in order:**
  1. `kubectl exec deploy/minecraft -n minecraft -- rcon-cli <cmd>` when the
     server image ships an RCON client. Detection is at runtime
     (`command -v rcon-cli || command -v mcrcon`) rather than assumed, because
     the Minecraft manifests were being written in parallel and did not exist
     when this was written — so the script adapts to whichever image is chosen
     instead of guessing. `mcrcon` is handled too (it needs explicit args).
  2. Otherwise the original raw-socket RCON client, pointed at the RCON
     Service's **ClusterIP**, resolved with `kubectl` each run. ClusterIPs are
     reachable from the node itself (kube-proxy programs the same rules for
     host-originated traffic), so no NodePort is needed. If the configured
     Service name is missing, it falls back to scanning the namespace for any
     Service exposing 25575.
- **PVC host path resolved dynamically.** `kubectl get pv -o json`, filtered on
  `spec.claimRef` matching `minecraft/minecraft-world`, reading
  `spec.hostPath.path` or `spec.local.path` (local-path-provisioner emits either
  depending on version). Hardcoding `pvc-<uuid>_minecraft_minecraft-world` would
  work until the first PVC recreate and then silently back up nothing.
- **Hibernation branching removed** (msh retired; always-on at Xmx6G). Replaced
  by a **readiness** check: if `deployment/minecraft` has no Ready replica
  (mid-rollout, crash-loop, image pull), the RCON save is skipped with a clear
  log line and **the on-disk data is still archived**. A pod that is down is not
  writing to the world, so a slightly stale archive is both safe and much better
  than a failed backup.
- **Refuses to produce an empty archive.** It always includes `world`; on
  pre-26.2 layouts it also includes `world_nether` and `world_the_end`. Paper
  26.2 migrated those dimensions under `world/dimensions`, so archiving the
  `world` directory captures all three dimensions instead of producing a
  misleadingly small tarball.
- **`save-on` failure gets its own alert.** Leaving autosave off is worse than a
  failed backup, and it used to be able to fail inside `finally` and be masked.
- **Low-disk warning** on the backup filesystem (same 5900rpm `/dev/sda2` as the
  cluster; a full disk takes k3s down with it).

**Preserved:** 7-day retention and the same `world-backup-<ts>.tar.gz` naming and
prune logic, output to `/home/chase/minecraft-backups/`, Discord DM alerting,
env-var config style, and the `save-off` → `save-all flush` → 5s sleep → tar →
`save-on` sequence.

### Timer change

The unit needs no edit beyond the `ExecStart` path:

```ini
# ~/.config/systemd/user/minecraft-backup.service
ExecStart=/home/chase/docker/observability/node_exporter/.venv/bin/python3 /home/chase/k8s-homelab/scripts/minecraft_backup.py
```
then `systemctl --user daemon-reload`. The timer itself is unchanged.

The existing `EnvironmentFile=` still supplies `MINECRAFT_RCON_PASSWORD`,
`DISCORD_BOT_TOKEN`, `DISCORD_USER_ID`. **Remove or blank
`MINECRAFT_WORLD_DIR`** from that file (it currently points at
`/home/chase/minecraft`) — when it is set, it *pins* the path and disables PVC
resolution. That is deliberate (useful for restores), but it means a stale value
would keep backing up the old host directory forever after cutover. This is the
single easiest way to get a silently wrong backup, so check it.

---

## 3. `minecraft_exporter.py`

**What changed**

- **RCON host** is `$MINECRAFT_RCON_HOST` if set, else the RCON Service's
  ClusterIP resolved via `kubectl` and **re-resolved whenever a connection
  fails** (ClusterIPs change when a Service is recreated).

  *Why ClusterIP and not node IP + NodePort:* a NodePort puts an RCON port on
  the LAN, and RCON's handshake sends the password in cleartext. ClusterIPs are
  reachable from the node itself, so we get the in-cluster path with no new
  externally-reachable surface. The only cost is a `kubectl` dependency the
  script needs anyway for the world directory.

- **`minecraft_process_cpu_percent` and `minecraft_process_memory_bytes` are
  REMOVED** (recommendation (b)). They came from `psutil` scanning the host
  process table for `paper.jar`; from outside the pod's PID namespace that finds
  nothing, so they could only go stale or zero.

  They are deliberately not re-implemented by querying Prometheus/cAdvisor and
  re-exporting: that means Prometheus scraping this exporter to read back a value
  Prometheus already scraped from the kubelet one hop earlier — redundant, an
  extra failure domain, an extra scrape interval of staleness, and a permanent
  source of "why do these two panels disagree".

  **Tradeoff, stated plainly:** the metric *names* change, so every panel or
  alert rule referencing the old gauges must be rewritten.

  **For the dashboard agent** — the "Minecraft memory vs its 8Gi limit" panel
  should use cAdvisor directly:

  ```promql
  # used
  container_memory_working_set_bytes{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}
  # limit
  container_spec_memory_limit_bytes{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}
  # cpu cores
  rate(container_cpu_usage_seconds_total{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}[5m])
  # oom kills
  increase(container_oom_events_total{namespace="minecraft",pod=~"minecraft-.*"}[24h])
  ```

  `working_set` (not RSS) is the number the kubelet actually compares against the
  limit when deciding to OOM-kill, so the replacement panel is *more* correct
  than the gauge it replaces, not merely equivalent.

  **This has a Prometheus prerequisite.** The plan drops the standalone cadvisor
  container because "the kubelet exposes it natively" — that is true, but the
  kubelet serves it at `https://<node>:10250/metrics/cadvisor` behind
  authn/authz. Prometheus therefore needs a scrape job using its ServiceAccount
  token (`bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token`,
  `tls_config.ca_file: .../ca.crt`, `insecure_skip_verify: true` for the node's
  self-signed cert) and a ClusterRole granting `nodes/metrics`. Without that job,
  none of the four queries above exist and the panel is blank.

- **World size** (`minecraft_world_size_bytes`) is kept, with the PVC host path
  resolved the same way as in the backup script. It degrades to a logged warning
  rather than an exception — this process is what DMs you when Minecraft stops
  answering, and it must never fall over because a disk walk failed.
- **Down-alerts include pod state** (`CrashLoopBackOff` vs `ContainerCreating`
  vs a normal rollout). A rollout and a crash-loop produce identical socket
  errors; the difference decides whether you get out of bed. Cosmetic only, no
  new metrics.

**Preserved exactly:** `minecraft_tps_1m/5m/15m`, `minecraft_players_online`,
`minecraft_players_max`, `minecraft_server_up`, `minecraft_world_size_bytes`,
port 9202, the 15s poll, the low-TPS streak logic and thresholds, the alert
cooldown, and the startup DM.

### Deployment decision: it stays a **host systemd unit**

Recommended over moving it in-cluster, for three reasons:

1. **Monitoring independence.** This process is the thing that tells you
   Minecraft is down. In-cluster on a *single-node* cluster it shares every
   failure mode with its subject; a k3s or node problem silences the alarm
   precisely when it matters.
2. **No image to build.** There is no python image for this. In-cluster means
   either a new local build + `k3s ctr images import` cycle on every script
   edit, or a ConfigMap plus pip-install-at-startup. Both are more moving parts
   than the venv that already exists at
   `~/docker/observability/node_exporter/.venv`.
3. The migration plan explicitly prefers fewer simultaneous changes, and this is
   the same call already made for `node_exporter` (native binary on :9100, kept
   as a static scrape target because it is genuinely a host thing).

The cost is scraping it as a static node-IP target instead of by Service DNS —
identical to node_exporter, and accepted for the same reason. **No manifest is
provided**, on purpose: shipping one that references an image nobody builds
would be worse than shipping none. If it is ever moved in-cluster, set
`MINECRAFT_RCON_HOST=minecraft-rcon.minecraft.svc.cluster.local` and
`MINECRAFT_WORLD_DIR=<mount path>` and nothing in the script needs to change.

### Service change

```ini
# ~/.config/systemd/user/minecraft-exporter.service
ExecStart=/home/chase/docker/observability/node_exporter/.venv/bin/python3 /home/chase/k8s-homelab/scripts/minecraft_exporter.py
```
then `systemctl --user daemon-reload && systemctl --user restart minecraft-exporter`.
Same `EnvironmentFile`; same caveat about `MINECRAFT_WORLD_DIR` as above.

`psutil` is no longer imported (the venv can keep it; other scripts use it).

### Prometheus change

The existing job points at `host.docker.internal:9202`, which does not resolve
from an in-cluster Prometheus. Repoint to the node IP — the same edit the plan
already calls for on the `:9100` and `:9201` jobs:

```yaml
  - job_name: minecraft-exporter
    static_configs:
      - targets: ["192.168.40.208:9202"]
```

---

## One-time host permission fix (both python scripts)

Both scripts read the world data at
`/var/lib/rancher/k3s/storage/pvc-<uuid>_minecraft_minecraft-world`. The world
*files* are written by the container as UID 1000 (= `chase`), so they are
readable — but the intermediate directories under `/var/lib/rancher` are
root-owned and may not be traversable by a user-level systemd unit.

Check first:

```bash
sudo ls -ld /var/lib/rancher /var/lib/rancher/k3s /var/lib/rancher/k3s/storage
```

If the user cannot traverse them, either:

```bash
# grant traversal (does not expose file contents beyond what mode bits allow)
sudo chmod o+rx /var/lib/rancher /var/lib/rancher/k3s /var/lib/rancher/k3s/storage
```

or move `minecraft-backup.{service,timer}` to a **system** unit running as root
(`/etc/systemd/system/`), which sidesteps it entirely at the cost of root-owned
backup tarballs.

Both scripts fail loudly on this rather than producing empty output: the backup
raises with the exact `chmod` command, and the exporter logs a warning noting
that world-size gauges will sit at 0 (a permission problem otherwise looks
exactly like a healthy empty world).

---

## Assumptions

`/home/chase/k8s-homelab/minecraft/` was **empty** when these were written (the
manifests are being authored in parallel), and k3s was not yet installed, so
nothing here could be executed against a live cluster. Everything below is
assumed, and every one of them is an env var — no file edits needed to correct
them.

| Assumption | Env var | Used by |
|---|---|---|
| Deployment `minecraft` in ns `minecraft` | `MINECRAFT_DEPLOYMENT`, `MINECRAFT_NAMESPACE` | backup, exporter |
| PVC named `minecraft-world` | `MINECRAFT_PVC` | backup, exporter |
| PVC holds the whole server dir, worlds at `<pvc>/world` etc. | `MINECRAFT_WORLD_DIR` (pins the path) | backup, exporter |
| ClusterIP Service `minecraft-rcon` exposing 25575 | `MINECRAFT_RCON_SERVICE`, `MINECRAFT_RCON_PORT`, `MINECRAFT_RCON_HOST` | backup, exporter |
| Pods carry label `app=minecraft` | — (cosmetic; only the alert's pod-state hint) | exporter |
| Deployment `jmusicbot` / container `jmusicbot` in ns `jmusicbot` | `JMUSICBOT_DEPLOYMENT`, `JMUSICBOT_CONTAINER`, `JMUSICBOT_NAMESPACE` | auto-update |
| Manifest at `k8s-homelab/jmusicbot/jmusicbot.yaml` with a single-line `image: jmusicbot-custom:...` and the `CUSTOM-BUILD-NOTE` block | `JMUSICBOT_MANIFEST` | auto-update |
| `k3s` at `/usr/local/bin/k3s`, kubeconfig at `/etc/rancher/k3s/k3s.yaml` mode 644 | `KUBECONFIG` | all three |

Where the assumption is most likely to be wrong (the RCON Service name), both
python scripts fall back to scanning the namespace for a Service exposing 25575
rather than failing.

The PVC-path resolution and Discord-alert helpers are **duplicated** between the
two python scripts rather than factored into a shared module. That matches the
originals — the Discord helper was already duplicated — and keeps each file
independently copyable, which is what actually happens to these during a
cutover.
