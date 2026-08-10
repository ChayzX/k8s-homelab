# The two bespoke watchers — NOT migrated, and not silently droppable either

This covers `ghcr.io/chayzx/observability-watcher` and
`ghcr.io/chayzx/observability-network-exporter`, currently run by
`/home/chase/docker/observability/docker-compose.yml`. **Neither is deployed by
any manifest in this repo.** This file exists so that omission is a documented
decision, not an oversight discovered three weeks post-cutover when an alert
that used to fire doesn't.

---

## The problem, precisely

Both images are confirmed (`docker image inspect`) to be plain Python 3.12
scripts — `watcher.py` and `network_exporter.py` — run with no other tooling in
the image. Both currently bind-mount `/var/run/docker.sock:/var/run/docker.sock:ro`
and, per the Compose file's own comments, talk to it directly:

- `watcher`: "Tails logs + restart events for every container listed in
  `WATCH_CONTAINERS`... Image is built by GitHub Actions."
- `network-exporter`: "reads the same data `docker stats` uses
  (`container.stats()` via the Docker socket) and exposes it as Prometheus
  metrics instead" — explicitly built to work around cAdvisor's broken
  per-container network metrics on Docker Desktop.

k3s uses embedded **containerd** directly, with no Docker Engine and no
`dockerd` process. There is no `/var/run/docker.sock` for these to mount on the
new node **at all** — not "it moved," not "it needs a different path." The
socket these images were built against does not exist on this architecture.

**Their behavior in that state cannot be verified without reading their
source**, which is private (`ghcr.io/chayzx/*`, not fetched here) and outside
this task's read-only scope. Do not assume "fails gracefully" — an unhandled
`docker.errors.DockerException` on startup could as easily crash-loop as run
half-functional and silent. **Neither is included in any manifest in this repo
pending that source read.** Do not deploy either image as-is under k3s.

---

## What each would actually need to work under k3s

Both would need a full rewrite of their data-collection layer, not a
config change:

### `observability-watcher`

- Replace: Docker Events API subscription + container inspect (via
  `docker.sock`) used to detect container restarts/deaths.
- With: the Kubernetes API — watch Pod status via a `ServiceAccount` granted
  `get`/`list`/`watch` on `pods` (and ideally `events`) in the namespaces it
  cares about. `pod.status.containerStatuses[].restartCount` and
  `.state.waiting.reason` / `.state.terminated.reason` replace what the Docker
  Events stream gave it.
- Replace: `WATCH_CONTAINERS=jmusicbot,pantry-bot-bot-1` (Docker container
  names) — those names do not exist as concepts in k8s. The equivalent
  selector is a label (`app.kubernetes.io/name=jmusicbot`) plus a namespace,
  which is a different filtering model, not a drop-in rename.
- This is a genuine k8s client library integration (e.g. the `kubernetes`
  PyPI package), a new `RESTART_THRESHOLD`/`RESTART_WINDOW_SECONDS` semantics
  (k8s already tracks `restartCount` cumulatively, which does not map 1:1 onto
  a sliding-window count), and new RBAC. Not a config flag.

### `observability-network-exporter`

- Replace: `container.stats()` over `docker.sock` for per-container network
  I/O.
- With: nothing needs building — **this one is arguably fully redundant under
  k8s, not just broken.** The whole reason this exporter exists (per its own
  Compose comment) is that cAdvisor's network metrics were broken *specifically
  under Docker Desktop's VM networking*, requiring `pid: host` and misreporting
  every container as the host's tunnel interfaces. That failure mode is a
  Docker Desktop artifact. Bare-metal k3s's kubelet exposes cAdvisor-derived
  container network metrics natively
  (`container_network_receive_bytes_total`, `container_network_transmit_bytes_total`,
  labeled by pod/namespace) with no `pid: host` trick required, because pods
  have real veth interfaces on a real Linux network namespace, not a nested VM.
  Before writing a single line of a k8s port of this exporter, scrape the
  kubelet's `/metrics/cadvisor` endpoint from Prometheus and check whether it
  already reports correct per-pod numbers — it very likely does, which would
  make this exporter unnecessary rather than portable.

---

## The k8s-native alternative (recommended over porting either script)

Both bespoke watchers exist to answer two questions Prometheus + Alertmanager
already answer natively, once **kube-state-metrics** is added to the
`observability` namespace (out of scope for this directory, but this is where
it plugs in):

| Bespoke watcher today | k8s-native replacement |
|---|---|
| `observability-watcher`: container down / restart-looping | Alert on `kube_pod_status_phase{phase!="Running"}` sustained, and on `rate(kube_pod_container_status_restarts_total[10m]) > 0` |
| `observability-network-exporter`: per-container network I/O | `container_network_receive_bytes_total` / `container_network_transmit_bytes_total`, already exposed by every kubelet at `/metrics/cadvisor` — no separate exporter needed at all |

Both route to Alertmanager, which supports a Discord receiver directly — the
**same Discord webhook** `observability-watcher` already posts to can be reused
as an Alertmanager receiver target, so the on-call experience doesn't change,
only the plumbing behind it.

This is very likely less total work than porting either script: no Kubernetes
Python client, no RBAC design for a bespoke ServiceAccount, no bespoke restart
window logic to reimplement — kube-state-metrics + a handful of PromQL alert
rules is standard, well-documented k8s observability, versus maintaining two
more one-off Python services with a Discord integration each.

---

## Migration-window hazard — act on this BEFORE Phase 2 (jmusicbot) starts

`observability-watcher` is watching Docker container **names**:
`WATCH_CONTAINERS=jmusicbot,pantry-bot-bot-1`. The instant either app's Compose
container is stopped as part of cutover — even though the replacement Pod
comes up healthy seconds later under k3s — the watcher sees the named Docker
container vanish and will fire a false "container down" Discord alert. It has
no way to know the workload relocated; from its point of view a container it
was told to watch simply stopped existing.

**Action, per the migration plan (Phase 2 entry, before touching
jmusicbot/pantry-bot):**

```bash
# in /home/chase/docker/observability/.env or the compose environment block:
WATCH_CONTAINERS=
```

then restart the `watcher` container so the empty list takes effect, and pause
the matching Uptime Kuma monitors for the same containers. Restore/retire
`observability-watcher` entirely once its k8s-native replacement (above) is in
place — do not re-populate `WATCH_CONTAINERS` with post-migration Docker
container names, since after Phase 5 there won't be any.
