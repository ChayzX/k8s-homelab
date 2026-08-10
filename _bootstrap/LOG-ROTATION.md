# kubelet / containerd container log rotation — findings + recommended change

**Status: NOT APPLIED.** This is a research + recommendation document only. The
agent that wrote this touched no live system state, ran no `kubectl`/`systemctl`/
`k3s` commands, and made no changes under `/etc/rancher/k3s` or
`/etc/systemd/system`. Applying the change below **requires restarting the k3s
service** on `MinecraftMachine` — do that by hand, deliberately, not as a side
effect of an unrelated apply.

## What this actually gates

Every container's stdout/stderr is captured by containerd to
`/var/log/containers/*.log` (symlinked from `/var/log/pods/<ns>_<pod>_<uid>/<container>/*.log`),
which is exactly what promtail tails (see `observability/promtail-config.yaml`
and the `promtail` DaemonSet's hostPath mount in ARCHITECTURE.md section 4).
kubelet — not containerd — owns rotation of these files, via two settings:

| Setting | What it controls |
|---|---|
| `containerLogMaxSize` (flag: `--container-log-max-size`) | Max size of one log file before kubelet rotates it |
| `containerLogMaxFiles` (flag: `--container-log-max-files`) | How many rotated files are kept per container before the oldest is deleted |

This is the mechanism that bounds pod stdout on disk — confirming it's sane is
what makes it safe to say (see the migration report) that pod stdout logs are
"already ephemeral by design," rather than just assumed to be.

## Findings

**Upstream kubelet defaults: `containerLogMaxSize: 10Mi`, `containerLogMaxFiles: 5`.**
Verified via the `KubeletConfiguration` (`kubelet.config.k8s.io/v1beta1`)
reference and multiple current sources — these have been the kubelet defaults
for a long time and are unchanged in the 1.36 line this cluster runs
(`v1.36.3+k3s1`, per ARCHITECTURE.md section 1). That means, worst case,
**10Mi × 5 = 50Mi of retained rotated stdout per container** before the oldest
rotation is deleted.

**k3s does not override these defaults.** No k3s release note, doc, or the
k3s-io/k3s discussion threads found in this research describe k3s shipping
different container-log-rotation defaults than upstream kubelet — k3s's
embedded kubelet inherits the standard 10Mi/5 unless a `--kubelet-arg` is
passed. (k3s *does* deliberately override other things, e.g. this cluster's
own `system-reserved`/`eviction-hard` kubelet args from the install — log
rotation is not one of the areas it touches by default.)

**The `--container-log-max-size`/`--container-log-max-files` flags are
deprecated upstream** in favor of setting `containerLogMaxSize`/
`containerLogMaxFiles` via a `KubeletConfiguration` file passed with
`--config`. They are **not removed** — k3s's own current documentation and
community guides (as recently referenced as 2023–2025) still describe passing
them via `--kubelet-arg=container-log-max-size=...` as the standard, working
way to do this in k3s. Both paths are viable; the `--kubelet-arg` path is
simpler for this single-node home-lab and matches how the other two kubelet
args already in this install (`system-reserved`, `eviction-hard`) are set, so
recommending consistency with the existing pattern rather than introducing a
second configuration mechanism (a `KubeletConfiguration` file) for one setting.

## Is 50Mi/container already fine?

Roughly, yes — with ~11 containers across all namespaces (jmusicbot,
jmusicbot-release-notifier, pantry-bot, cloudflared, loki, prometheus,
grafana, uptime-kuma, promtail, kube-state-metrics, minecraft, keel), worst
case is on the order of ~550Mi total, trivial next to the 20Gi/20Gi/10Gi PVCs
already provisioned for Loki/Prometheus/Minecraft on the same HDD. This is
**not an urgent risk** the way Loki's retention was (fix #1) — it's already a
bounded default, not an unbounded one.

That said, it's currently bounded only *implicitly* — nothing in this repo's
install flags states it, unlike every other kubelet tuning decision here
(`system-reserved`, `eviction-hard`), which are explicit and commented in
ARCHITECTURE.md. Recommending making it explicit, with a modest tightening
consistent with this repo's general "the HDD is the bottleneck, be
deliberate" posture — not because 50Mi/container is dangerous, but because an
implicit default silently inherited from upstream kubelet is exactly the kind
of thing this repo otherwise doesn't leave to chance.

## Recommended change

Reduce `containerLogMaxFiles` from 5 to 3 (cuts worst-case per-container
footprint from 50Mi to 30Mi — plenty of on-disk history for a `kubectl exec`
spot-check, since Loki is now the durable copy per fix #1), leave
`containerLogMaxSize` at the default 10Mi (already a sane per-file chunk size,
no reason to shrink it and cause more frequent rotation I/O on the HDD).

**Apply via `/etc/rancher/k3s/config.yaml`** (does not currently exist on this
host — confirmed by this agent, read-only check, no sudo used/available in
this session). This file is read additively by k3s on every start regardless
of how the service was originally installed, so it layers on top of the
existing systemd unit's flags (`--write-kubeconfig-mode 644
--kubelet-arg=system-reserved=cpu=1000m,memory=3Gi
--kubelet-arg=eviction-hard=memory.available<500Mi`) without needing to edit
`/etc/systemd/system/k3s.service` itself:

```yaml
# /etc/rancher/k3s/config.yaml
kubelet-arg:
  - "system-reserved=cpu=1000m,memory=3Gi"
  - "eviction-hard=memory.available<500Mi"
  - "container-log-max-files=3"
  - "container-log-max-size=10Mi"
```

(Repeating the two existing `kubelet-arg` entries above is deliberate, not
optional — if `config.yaml` only lists the two new ones, confirm whether k3s
merges kubelet-arg lists across the systemd ExecStart and config.yaml or
whether one source wins; the safe move is to make config.yaml the single
complete list.)

Equivalently, as a one-line `INSTALL_K3S_EXEC` addition if the install is ever
rerun from scratch:

```
INSTALL_K3S_EXEC="--write-kubeconfig-mode 644 \
  --kubelet-arg=system-reserved=cpu=1000m,memory=3Gi \
  --kubelet-arg=eviction-hard=memory.available<500Mi \
  --kubelet-arg=container-log-max-files=3 \
  --kubelet-arg=container-log-max-size=10Mi"
```

**Applying this requires `sudo systemctl restart k3s`** — a control-plane and
kubelet restart, on the only node in the cluster, which briefly interrupts the
kubelet's pod-management loop (running pods generally survive a kubelet
restart, but this is still a live-cluster action, which is exactly the kind
of thing this file exists to *not* do unattended). Apply by hand, at a
convenient moment, not as part of an automated pass.

## Sources consulted

- Kubernetes `KubeletConfiguration` reference (`k8s.io/kubelet/config/v1beta1`) — `containerLogMaxSize`/`containerLogMaxFiles` defaults
- k3s-io/k3s GitHub discussions on `--kubelet-arg=container-log-max-*` usage
- Community writeups (unixpowered.com and others) confirming the `--kubelet-arg` mechanism and that CLI flags remain functional despite upstream deprecation in favor of the KubeletConfiguration file
