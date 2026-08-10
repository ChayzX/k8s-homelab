# Minecraft cutover runbook

Operator-executed, serial, by hand. This is the highest-risk piece of the
whole homelab migration: a live server with real players and a 1.3G world.
Read the whole document once before running anything. Do not skip ahead.

## Before you start — the shape of the risk

- **This is not a seamless verify-then-flip.** The node currently sits around
  ~8-9Gi used out of ~12.3Gi allocatable (`kubectl top node`) with Docker
  Desktop's containers still running. msh's JVM is configured for
  `-Xmx12G` and the new pod's limit is `8Gi`. **Those two cannot be
  memory-resident on this node at the same time** — running both risks a
  host-level OOM that has nothing to do with either JVM's own heap
  management, and an OOM mid-write is exactly the corruption this whole
  design exists to avoid. Consequently `msh.service` must be stopped
  **before the isolated NodePort verification test starts**, not merely
  before the final port cutover. That stop is a real, if brief, outage for
  any currently-connected players — plan it as such, do not run it silently.
- **Point of no return: the first player join on the new pod.** Up to that
  moment every step below is cheaply reversible. After it, rolling back to
  `msh` silently serves the *old, frozen* world and discards everything
  played since — see **Rollback**, at the end, before you flip the
  production Service.
- **`java` must be PID 1 in the running container**, or SIGTERM never reaches
  it and it gets SIGKILLed at the grace deadline. This is verified explicitly
  in Step 5 below. Do not skip that check.

## Prerequisites

- k3s is installed and healthy (`kubectl get nodes` shows `Ready`).
- `local-path` is the default (or only) StorageClass:
  `kubectl get storageclass`.
- You are on the same host as the k3s node — this whole runbook assumes
  single-node k3s where "the node's disk" and "this machine's disk" are the
  same filesystem, which is what makes the direct-`sudo`-on-hostPath steps
  below valid. It would not work this way on a multi-node cluster.
- `docker` still works on this host (native `docker-ce`, kept running for
  exactly this purpose per the migration plan) — used only to `build` and
  `save` the image; k3s never pulls from a registry for this workload.

---

## Step 1 — Build the image

The build context needs the exact jar that is live today.

```bash
cp /home/chase/minecraft/paper.jar /home/chase/k8s-homelab/minecraft/paper.jar
cd /home/chase/k8s-homelab/minecraft

# Confirm the copy matches the running jar bit-for-bit before building on it.
sha256sum /home/chase/minecraft/paper.jar paper.jar

docker build -t localhost/paper-minecraft:1.21.11-b127 .
```

The tag `1.21.11-b127` mirrors the build already installed
(`version_history.json` on the live server: `1.21.11-127-bd74bf6`). If you
rebuild against a newer Paper build later, bump the tag — `minecraft.yaml`'s
`image:` field must be updated to match, on purpose, so a Paper upgrade is a
visible, reviewable, one-line diff rather than a silent `:latest` pull.

Sanity-check the image before it goes anywhere near the cluster:

```bash
docker run --rm --entrypoint sh localhost/paper-minecraft:1.21.11-b127 \
  -c 'id; java -version; ls -l /opt/minecraft'
# expect: uid=1000(minecraft) gid=1000(minecraft); openjdk 21.x
```

## Step 2 — Side-load into k3s containerd

k3s does not share Docker's image store — it has its own embedded containerd.
The image must be exported from Docker and imported into it directly; nothing
is pulled from a registry.

```bash
docker save localhost/paper-minecraft:1.21.11-b127 | sudo k3s ctr images import -

# Confirm it landed in the containerd k3s actually uses:
sudo k3s ctr images ls | grep paper-minecraft
```

`imagePullPolicy: IfNotPresent` in `minecraft.yaml` depends on this step
having already happened — if it's skipped, the pod sits in
`ErrImageNeverPull`/`ImagePullBackOff` since there is nowhere for kubelet to
pull from.

## Step 3 — Bootstrap the namespace, ServiceAccount, PVC, and pre-cutover Services

Apply only the objects labelled `pre` — this deliberately excludes the
production `minecraft` LoadBalancer Service (see the big warning at the top
of `minecraft.yaml` for why applying the whole file early is unsafe: it would
make ServiceLB install a hostPort 25565 DNAT rule and hijack live traffic
from `msh` before anything has been verified).

```bash
cd /home/chase/k8s-homelab/minecraft
kubectl apply -f minecraft.yaml -l homelab.chase/cutover-stage=pre
```

This creates: the `minecraft` namespace, `minecraft-sa`, the
`minecraft-world` PVC (still `Pending` — `local-path` is
`WaitForFirstConsumer`, so no disk directory exists until something is
scheduled against it), the `minecraft-rcon` ClusterIP Service, and the
`minecraft-nodeport` verification Service. The `minecraft` Deployment is also
created here (it carries `cutover-stage: pre` on the Deployment object
itself, even though its production-facing Service does not) — it will fail
to become ready, on purpose, until Step 4 is done. That's expected; see Step
4.

Now create the RCON secret — **follow `secrets.md`**, not literals typed
here. Do this before Step 4 or the pod sits in `CreateContainerConfigError`.

```bash
kubectl get secret minecraft-rcon -n minecraft
# if not found, stop and go do secrets.md now
```

## Step 4 — Force PVC provisioning, then copy the world data in

`WaitForFirstConsumer` means the PVC only becomes `Bound` — and the
`local-path` host directory only gets created — once a pod that mounts it is
*scheduled*. The Deployment applied in Step 3 is that pod. It will fail
(fast, on purpose — the entrypoint's empty-data-dir guard in
`entrypoint.sh`), but scheduling it is enough to trigger provisioning.

```bash
kubectl get pods -n minecraft -w
# wait for the minecraft-xxxx pod to leave Pending; Ctrl-C once it does
kubectl get pvc minecraft-world -n minecraft
# expect STATUS: Bound
```

Confirm the entrypoint's guard actually fired (this is also your first live
proof the fail-fast check works — a real value, not just a formality):

```bash
kubectl logs -n minecraft deploy/minecraft
# expect: "FATAL: /data/server.properties not found. ... Refusing to start,
# because Paper would generate a NEW world here."
```

Stop the crash loop while you copy (the PVC and its data persist independent
of replica count):

```bash
kubectl scale deployment/minecraft -n minecraft --replicas=0
```

Find the exact on-disk directory backing the PV — do not guess a path:

```bash
PV_NAME=$(kubectl get pvc minecraft-world -n minecraft -o jsonpath='{.spec.volumeName}')
PV_PATH=$(kubectl get pv "$PV_NAME" -o jsonpath='{.spec.hostPath.path}')
echo "$PV_PATH"
```

### Optional: shrink the eventual downtime window now, while the server is still live

Because this host and the k3s node are the same machine, you can `rsync` the
bulk of the 1.3G world directly, without touching `/home/chase/minecraft`
(read-only on the source) and without stopping anything yet. This *pre-copy*
is not the authoritative copy — players are still connected through `msh` and
still writing to the world — it just warms the destination so the final,
authoritative copy (done after `msh` is stopped, below) only has to transfer
the delta.

```bash
sudo rsync -a --info=progress2 --exclude='.console_history' \
  /home/chase/minecraft/ "$PV_PATH"/
```

This step is safe to skip if you'd rather take the full ~1.3G copy time
inside the downtime window instead of splitting it.

### The downtime window starts here

Everything from here through "flip the production Service" (Step 8) happens
with `msh` stopped. Stopping it does two things at once, both required:

1. **Releases the memory** — kills the resident JVM (up to 12G configured),
   which is what makes room for the verification pod's up-to-8Gi limit on
   this memory-constrained node.
2. **Releases TCP port 25565** — `msh` is what's listening there today
   (`MshPort: 25565` in `msh-config.json`); nothing else can bind it while
   `msh.service` is up.

If you'd rather not take a deliberate outage, the alternative is timing this
to one of `msh`'s own hibernation windows (`TimeBeforeStoppingEmptyServer:
1800` — 30 minutes idle) when its JVM is not resident anyway. This is
lower-certainty (a player can reconnect and wake it at any moment, right in
the middle of your copy) and buys you nothing on the port-25565 constraint
later at Step 8 regardless. **Recommended: just stop it deliberately.**

```bash
# Warn anyone currently on the server first if you can.
sudo systemctl stop msh.service

# Confirm both effects:
sudo ss -ltnp | grep 25565            # expect: nothing bound
pgrep -fa 'java.*paper.jar'           # expect: no output (JVM not resident)
```

Now take the **pre-migration backup** — the last snapshot of the server in
its known-good, definitely-still-bare-metal state, independent of everything
that follows:

```bash
mkdir -p /home/chase/minecraft-backups
tar -C /home/chase/minecraft -czf \
  /home/chase/minecraft-backups/world-backup-pre-migration-$(date +%Y%m%dT%H%M%S).tar.gz \
  world world_nether world_the_end
```

Now do the authoritative copy (a second `rsync` pass, safe and fast if you
did the optional pre-copy above; a full ~1.3-1.6G copy if you didn't):

```bash
sudo rsync -a --delete --info=progress2 --exclude='.console_history' \
  /home/chase/minecraft/ "$PV_PATH"/
```

`--delete` matters here: it makes the PVC copy an exact mirror of the
bare-metal source, not an accumulation of whatever the earlier pre-copy left
behind.

Fix ownership — every file at the source is `1000:1000`, and `local-path`'s
directory was created root-owned:

```bash
sudo chown -R 1000:1000 "$PV_PATH"
ls -la "$PV_PATH" | head
# expect: everything owned 1000 1000, world/ world_nether/ world_the_end/
#         server.properties, eula.txt, paper.jar (harmless duplicate; the
#         image also carries its own at /opt/minecraft/paper.jar) all present
```

## Step 5 — Bring the pod up and verify PID 1

```bash
kubectl scale deployment/minecraft -n minecraft --replicas=1
kubectl get pods -n minecraft -w
# wait for Running / READY 1/1; Ctrl-C once it is
```

**Verify java is PID 1 before doing anything else.** This is the single
check that protects the world from a SIGKILL-on-shutdown corruption, and it
costs ten seconds:

```bash
kubectl exec -n minecraft deploy/minecraft -- cat /proc/1/cmdline | tr '\0' ' '; echo
```

The output must start with the java binary path (e.g.
`/opt/java/openjdk/bin/java -Xms2G -Xmx6G ...`). If it starts with
`/bin/sh` or `/usr/local/bin/entrypoint.sh`, **stop** — the `exec` in
`entrypoint.sh` did not take effect (check for an accidental `command:` /
`args:` override in the Deployment that reintroduces a shell wrapper) and
must be fixed before anyone connects.

Tail the logs and confirm a clean startup against the *real* world (not a
freshly generated one — check for the actual spawn chunk / plugin load
messages you'd recognize from the live server, and confirm player count /
whitelist entries look right once RCON is reachable):

```bash
kubectl logs -n minecraft deploy/minecraft -f
```

## Step 6 — Verify RCON in-cluster

```bash
kubectl exec -n minecraft deploy/minecraft -- \
  java -jar /opt/minecraft/rcon.jar list
# expect: "There are 0 of a max of 20 players online: " (or similar)
```

If this fails with an auth error, `RCON_PASSWORD` (from the `minecraft-rcon`
Secret) does not match `rcon.password` in the copied `server.properties` —
re-check `secrets.md`.

## Step 7 — Isolated verification on the NodePort

Connect a Minecraft client to:

```
192.168.40.208:30565
```

Verify, in order:
- You spawn into the **existing** world at the **existing** spawn point, not
  a fresh one.
- Whitelisted/OP status is intact (`ops.json`, `whitelist.json` carried over).
- Place a block, `kubectl delete pod -n minecraft -l app.kubernetes.io/name=minecraft`,
  wait for the replacement pod to become Ready, rejoin, confirm the block
  persisted (this is the standard "did the volume actually round-trip"
  check, and it's cheap to do now while nothing public depends on it yet).
- `kubectl top pod -n minecraft` — heap/working-set behaving sanely, nowhere
  near the 8Gi limit at idle.

**This step still has `msh` stopped and the real server unreachable on
25565.** Keep this phase short. If anything here fails, this is your
cheapest rollback point — see **Rollback, Case A** below — and it costs
nothing beyond the outage already in progress: nobody has played on the new
pod yet.

## Step 8 — Second backup, then flip

Only proceed past this point once Step 7 passed cleanly. This is the actual
point of no return coming up — the moment a player joins on the production
Service, rollback stops being free.

Take the **cutover snapshot** — a tarball of the world exactly as it's about
to be handed to the public Service. This is what makes a post-join rollback
*restore* progress instead of silently *reverting* it (see Rollback):

```bash
kubectl exec -n minecraft deploy/minecraft -- \
  java -jar /opt/minecraft/rcon.jar save-all flush

sudo tar -C "$PV_PATH" -czf \
  /home/chase/minecraft-backups/world-backup-cutover-$(date +%Y%m%dT%H%M%S).tar.gz \
  world world_nether world_the_end
```

Confirm `msh.service` is still stopped (it should be, from Step 4) and now
**disable** it — it is being retired, not paused:

```bash
sudo systemctl disable msh.service
sudo systemctl is-active msh.service   # expect: inactive
sudo systemctl is-enabled msh.service  # expect: disabled
```

Flip the production Service — this is the only remaining apply, and it's
exactly the LoadBalancer Service that was withheld in Step 3:

```bash
kubectl apply -f minecraft.yaml
kubectl get svc -n minecraft minecraft
```

k3s's ServiceLB will spawn an `svclb-minecraft-*` pod that binds hostPort
25565. Confirm it actually bound (a stray process still holding the port —
unlikely now, but check — would leave this Pending):

```bash
kubectl get pods -n minecraft -l svccontroller.k3s.cattle.io/svcname=minecraft
sudo ss -ltnp | grep 25565
# expect: bound, owned by a process under the containerd/k3s cgroup, not msh
```

### No external-facing config change needed

Unlike an earlier version of this plan, **playit.gg is not part of the live
path** — the router already has a port-forward rule sending inbound traffic
directly to `192.168.40.208:25565` (this host's LAN IP). That rule doesn't
care what's listening on the other end; once the Service above is bound, the
existing forward just works. There is nothing to reconfigure externally at
cutover.

`playit.service` is still `active`/`enabled` on this host — it's a leftover
from before the router-level forward existed, not a live dependency. Disable
it as part of cutover cleanup so it doesn't sit there consuming a system slot
and confusing the next person who reads `systemctl list-units`:

```bash
sudo systemctl stop playit.service
sudo systemctl disable playit.service
```

Do **not** do this before the Service is confirmed bound and joinable —
keep it as the very last cleanup action, in case something about the router
forward's target needs playit as a fallback path you haven't accounted for.

## Step 9 — Confirm publicly, then soak

Have a second person (or your own phone off wifi) join from outside the LAN
to confirm the router forward reaches the pod end to end. Then watch it for
real play, not just a smoke test:

```bash
kubectl logs -n minecraft deploy/minecraft -f
kubectl top pod -n minecraft
```

## Step 10 — Update the two host-side scripts

These two scripts live outside this directory
(`/home/chase/docker/observability/monitoring/minecraft-exporter/`) and are
out of scope to edit here, but both currently talk to the bare-metal server
and will silently stop working (or, worse, silently report stale/wrong data)
the moment `msh`/the old JVM is gone. Fix both before trusting this
unattended:

**`minecraft_backup.py`** — currently opens a raw RCON socket to
`localhost:25575` (`RCON_HOST = "localhost"` at the top of the file). That
address no longer has anything listening on it. Replace the RCON call with a
`kubectl exec` invocation of the same `rcon.jar` used by the pod's own
`preStop` hook, e.g.:

```python
subprocess.run(
    ["kubectl", "exec", "-n", "minecraft", "deploy/minecraft", "--",
     "java", "-jar", "/opt/minecraft/rcon.jar", "save-all", "flush"],
    check=True, timeout=30,
)
```

This sidesteps needing network reachability to the pod entirely — `kubectl
exec` goes through the API server, so it works regardless of CNI addressing.
`WORLD_DIR` also needs to change from `/home/chase/minecraft` to `$PV_PATH`
(the `local-path` hostPath directory found in Step 4) — the world it should
be tarring now lives there, not in the old bare-metal directory (which is
frozen at the cutover snapshot and will never change again).

**`minecraft_exporter.py`** — same `RCON_HOST = "localhost"` problem, plus it
shells out to a local `psutil` process lookup for JVM stats that no longer
exists as a bare process on this host. Two changes:
- RCON: point it at the ClusterIP directly (not the DNS name — this script
  runs as a plain systemd user service on the host, not inside a pod, so it
  never gets CoreDNS in its resolv.conf; the numeric ClusterIP is reachable
  from the host because kube-proxy programs host-level rules for it, single-
  node cluster == same network namespace as the node):
  ```bash
  kubectl get svc minecraft-rcon -n minecraft -o jsonpath='{.spec.clusterIP}'
  ```
  Set that as `RCON_HOST` in the exporter's `.env`
  (`/home/chase/docker/observability/monitoring/minecraft-exporter/.env`).
- Process stats: replace the `psutil` process lookup with
  `container_memory_working_set_bytes` from cAdvisor (kubelet exposes it
  natively — no extra install), scoped to the `minecraft` pod, as the
  Prometheus/observability side of this migration already plans to do for
  every other app's dropped host-level process metrics.

---

## Rollback

Which case applies depends entirely on **whether a player has joined the pod
on the production Service (port 25565) yet.**

### Case A — before the Service flip (Steps 4-7)

Cheap, no data at risk. Nobody outside the NodePort test has touched the new
world; `msh` still has the only copy anyone has played on.

```bash
kubectl scale deployment/minecraft -n minecraft --replicas=0
sudo systemctl start msh.service
sudo systemctl status msh.service   # confirm it's back up and listening
```

Investigate at leisure. The PVC and its copy are left in place — no need to
delete anything — you can retry Step 5 onward once whatever was wrong is
fixed.

### Case B — after the Service flip, before any player has joined

Still cheap. Reverse Step 8's Service change and bring `msh` back:

```bash
kubectl delete -f minecraft.yaml -l homelab.chase/cutover-stage=cutover
sudo systemctl enable --now msh.service
```

No progress exists yet that isn't already in the cutover-snapshot tarball,
so nothing is lost.

### Case C — after a player has joined the pod on the production Service

**Read this before you touch anything.** The bare-metal
`/home/chase/minecraft` world has been frozen since Step 4 — it has not
changed since `msh` was stopped. Meanwhile the PVC's world has kept moving:
anything a player has built, mined, or lost since they joined exists **only**
in the PVC, not in the cutover-snapshot tarball and not in
`/home/chase/minecraft`.

If you restart `msh` now, players get silently dropped back into the frozen,
older world with no warning that anything they just did is gone. That is a
revert, not a rollback, and it happens with zero error message — the server
will look completely fine, just quietly out of date.

**If you must move off the pod after this point, do not restore the old
bare-metal copy — promote the PVC's current state to bare-metal instead, so
nothing is lost:**

```bash
# 1. Take an immediate snapshot of the CURRENT PVC state before touching
#    anything else — this captures whatever just went wrong plus all real
#    player progress, and gives you a point you can always get back to.
kubectl exec -n minecraft deploy/minecraft -- \
  java -jar /opt/minecraft/rcon.jar save-all flush
sudo tar -C "$PV_PATH" -czf \
  /home/chase/minecraft-backups/world-backup-rollback-$(date +%Y%m%dT%H%M%S).tar.gz \
  world world_nether world_the_end

# 2. Stop the pod cleanly (this still goes through the normal preStop + SIGTERM
#    path, so it's a clean shutdown, not a yank).
kubectl scale deployment/minecraft -n minecraft --replicas=0

# 3. Copy the PVC's now-current world OVER the stale bare-metal copy —
#    the reverse direction of Step 4's copy — so bare-metal resumes with
#    everything players did on the pod intact.
sudo rsync -a --delete \
  "$PV_PATH"/world "$PV_PATH"/world_nether "$PV_PATH"/world_the_end \
  /home/chase/minecraft/
sudo chown -R chase:chase /home/chase/minecraft/world \
  /home/chase/minecraft/world_nether /home/chase/minecraft/world_the_end

# 4. Resume on bare metal.
sudo systemctl enable --now msh.service
```

Only fall back to a true revert (restart `msh` untouched, accept the loss) if
the PVC's data is itself the thing that's broken (disk corruption, a bad
`local-path` write) and step 1 above isn't possible. In that case the
cutover-snapshot tarball from Step 8 is the newest guaranteed-good copy you
have, and the loss is exactly "everything since that tarball was taken" —
say so plainly to anyone who was playing, don't discover it silently with
them.
