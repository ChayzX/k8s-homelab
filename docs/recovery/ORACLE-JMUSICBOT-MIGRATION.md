# Oracle independent-environment migration: JMusicBot

## Why JMusicBot is the first candidate

JMusicBot is the least coupled candidate for the first independent move:

- the published image is multi-architecture and does not call the Kubernetes
  API;
- Discord and YouTube are outbound dependencies;
- mutable state is restored from and synchronized to the versioned R2 prefix
  `r2:pantry-bot-backups/jmusicbot/`;
- the checked-in Deployment already uses `Recreate`, `emptyDir`, an R2 restore
  init container, and a five-minute R2 sync sidecar.

Opsbot is not the first candidate: its useful function depends on the home
Kubernetes API and its in-cluster ServiceAccount/RBAC. PantryBot and
Authentik/Postgres are stateful or writer-sensitive and require stronger
fencing/promotion tests.

This runbook establishes Oracle as a **continuously active environment** with a
live process that has no Discord side effects unless it owns the
resource-scoped witness lease. Oracle's egress capacity is measured and
bounded, not used to power the service off. Ownership transfer is an explicit,
fenced operation: the new site acquires the next witness epoch and the old
process must lose renewal and shut down before the new site connects.

## Oracle standby contract

The recovery overlay is a warm-capacity overlay, not a second Discord writer.
Its single replica is expected to be `Running` but `NotReady` while home owns
the `jmusicbot` witness lease. In that state the `/health` probe returns 503,
the health Service has no endpoint, and the application must not log Discord
`READY` or create the ownership marker. `/live` remains independent and is
used only to distinguish a live, fenced process from a dead process. The R2
sidecar may restore state, but it syncs only while the marker exists and never
copies the marker itself.

The repository contract for these invariants is executable and non-live:

```bash
bash tests/jmusicbot-oracle-standby-test.sh
```

Do not “fix” an expected standby `NotReady` by bypassing the witness or by
making readiness depend on `/live`; investigate only when the lease state,
Discord logs, marker, or sidecar behavior contradicts this contract.

## Gates before touching Oracle

- [x] Root access is available on `minecraftmachine` and Oracle.
- [x] The k3s datastore and matching server token are encrypted and stored
  outside the tower; an isolated restore check passed.
- [x] Chasebot has an authenticated Tailscale path, or an approved equivalent
  management path; its kubelet proxy returns `ok`.
- [x] Home Cloudflare connectors can run on home-only nodes. With Oracle still
  attached, verify PantryBot connector replicas are on distinct nodes; after
  Oracle reclamation, verify they are on `minecraftmachine` and chasebot.
- [x] Home CI connector remains available or is explicitly accepted as
  unavailable during the maintenance window.
- [x] R2 contains a fresh JMusicBot generation and the required independent R2
  credential is available without copying its value into Git.
- [x] The current home JMusicBot pod is healthy and its exact image digest,
  Secret names, and R2 object listing are recorded.
- [x] A Discord-side duplicate-work check is prepared; only one JMusicBot
  writer may run at a time.
- [x] The same neutral witness is provisioned in both clusters with resource
  `jmusicbot`; without this, active-active capacity is not safe to enable.

## Phase 1: record and protect the current environment

Run from the home control-plane host, without printing Secret values:

```bash
kubectl -n jmusicbot get deploy jmusicbot -o yaml > /secure/recovery/jmusicbot-home-deployment.yaml
kubectl -n jmusicbot get pod -l app.kubernetes.io/name=jmusicbot -o wide
kubectl -n jmusicbot get secret jmusicbot-config-txt jmusicbot-r2 -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
kubectl -n pantry-bot get deploy cloudflared -o wide
kubectl -n ci-tunnel get deploy cloudflared -o wide
```

Record the running image digest and the newest R2 object/checksum in Issue
#191. Do not export Secret data into the repository or an unencrypted file.

## Phase 2: detach Oracle from the home cluster — completed 2026-09-10

This is the destructive/privileged boundary. Preserve rollback material before
stopping the agent:

1. Verify the k3s datastore/token backup and record its checksum.
2. Capture the Oracle agent unit, k3s version, network addresses, and a list of
   current support workloads. The current Oracle workload set includes the
   shared Cloudflare connector, LDAP outpost, ServiceLB pods, node-exporter,
   alloy, and Opsbot.
3. Ensure home Cloudflare connector capacity is scheduled on
   `minecraftmachine` and chasebot; do not leave the shared tunnel with only
   one unverified connector.
4. Stop and disable only Oracle's home-cluster `k3s-agent`. Do not delete the
   saved agent unit or `/var/lib/rancher/k3s` until the independent environment
   has passed its first recovery test.
5. Install a pinned, single-server k3s instance on Oracle using its Tailscale
   address. Use the default SQLite datastore for this single independent
   server; do not use `--cluster-init` and do not join it to the home cluster.
6. Save the independent kubeconfig outside the repository with mode 600 and
   verify that the new server is reachable only through the intended
   management path.

The install artifact, checksum, server version, flags, and rollback location
must be recorded in Issue #191 before deploying an application.

## Phase 3: deploy the Oracle JMusicBot active process — completed 2026-09-10

Use a clean independent kubeconfig and apply only `jmusicbot/00-namespace.yaml`,
the JMusicBot ServiceAccounts, config Secret, R2 Secret, health Service, and main Deployment
from this repository. Do not apply the release notifier in this first move;
it has a separate webhook and local marker state.

Provision the two Secrets through the approved secret store, using the names
and keys documented in `jmusicbot/SECRETS.md`; never put their values in Git or
the migration issue. Apply the checked-in Deployment without changing its
immutable image reference. Verify:

For a Kubernetes-native recovery path, first create empty placeholder Secrets
with the documented keys using an operator identity, then apply
`docs/recovery/jmusicbot-secret-restore-rbac.yaml` to the independent target.
The resulting `jmusicbot-secret-restore` identity can only update or patch
`jmusicbot-config-txt` and `jmusicbot-r2`; it cannot list, create, delete, or
read other Secrets. Remove its RoleBinding after reconstruction. This manifest
is deliberately not part of the home bootstrap/apply set.

The first independent cutover intentionally excludes the release notifier and
the home-only PVC. From the migration operator's workstation, the repeatable
apply set is:

```bash
kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply -f jmusicbot/00-namespace.yaml
kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply -f jmusicbot/10-serviceaccounts.yaml
# Create jmusicbot-config-txt and jmusicbot-r2 in the approved secret store.
kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply -k docs/recovery/jmusicbot-oracle-standby
kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply -f jmusicbot/45-service-health.yaml
```

Do not apply `30-pvcs.yaml` or `50-deployment-release-notifier.yaml` during
the first move; they are not required by the main bot's R2-backed `emptyDir`
state and would add an untested writer/state surface.

```bash
kubectl --kubeconfig "$ORACLE_KUBECONFIG" -n jmusicbot rollout status deploy/jmusicbot --timeout=300s
kubectl --kubeconfig "$ORACLE_KUBECONFIG" -n jmusicbot get pod -o wide
kubectl --kubeconfig "$ORACLE_KUBECONFIG" -n jmusicbot port-forward svc/jmusicbot-health 19091:9091
curl -fsS http://127.0.0.1:19091/live
curl -fsS http://127.0.0.1:19091/health
```

Apply the main Deployment with `replicas: 1` after the `jmusicbot-witness`
Secret exists. It remains live but NotReady while home owns the `jmusicbot`
lease and does not open a Discord session. To transfer ownership, stop or
isolate the home pod, wait for the 30-second lease to expire, then verify
Oracle acquired the higher epoch and became Ready. If the witness Secret or
endpoint is unavailable, fail closed rather than bypassing the lease.

Validate the R2 restore log, Discord login, health readiness, five-minute sync
sidecar, and outbound access to required APIs. Record the start time, readiness
time, image digest, restored generation, and any observed data-loss window.

## Rollback

If Oracle deployment, login, R2 restore, or behavior validation fails:

1. Stop the Oracle JMusicBot process and wait for termination.
2. Confirm no independent Discord writer remains.
3. Restore the home Deployment to one replica using the previously recorded
   image and Secret names.
4. Verify the home health endpoint and Discord login.
5. Leave Oracle's independent server intact for diagnosis; do not rejoin it to
   the home cluster as an emergency shortcut.

If the independent k3s installation itself fails, restore the saved Oracle
agent unit and start the original agent only after confirming the home control
plane and home Cloudflare capacity are healthy. Record the rollback in Issue
#191.

## Promotion acceptance

The active-active migration is accepted only when all of the following are evidenced:

- exactly one JMusicBot writer is running in the home environment;
- both environments use the same witness authority and resource `jmusicbot`;
- the non-owner process has no Discord gateway session and is NotReady;
- after renewal loss, the old process closes Discord and exits before the next
  witness epoch is acquired;
- Oracle has the pinned multi-architecture image, required Secret names, and
  can restore the retained R2 generation without starting a writer;
- home public routes and the external monitor remain healthy;
- the home rollback path is executable;
- emergency SSH access does not depend on Authentik;
- the observed RPO/RTO and rollback outcome are recorded in GitHub Issue #191.

The Oracle standalone server and active process deployment were created on
2026-09-10. The R2 restore check recovered `serversettings.json` and
`youtubetoken.txt`; keep the Oracle Deployment at `replicas: 1` while the
witness lease/fencing rehearsal records evidence. The live non-owner process
is safe capacity, not a second Discord writer.
