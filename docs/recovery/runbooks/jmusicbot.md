# JMusicBot recovery and single-owner runbook

This runbook covers the Kubernetes JMusicBot deployment in the `jmusicbot`
namespace. It does not cover Minecraft. Home and Oracle may both have process
capacity, but exactly one site may hold the resource-scoped witness lease and
open a Discord session or write mutable R2 state.

## Safety contract

- The witness resource is independent of PantryBot and Opsbot leases.
- A lease holder is identified by the witness response (`site`, positive
  `epoch`, opaque token, and expiry). Never copy or print the token.
- A non-holder must remain running only as a standby: it must not connect to
  Discord and its `jmusicbot-health` endpoint must remain not ready (`503`).
- The `r2-sync` sidecar may write only when the lease marker exists; it excludes
  `.jmusicbot-lease-owner` from restore and sync. The marker is not authority.
- `Recreate` is required for a site-local rollout. Do not use a rolling update
  for the Discord writer.
- Never scale or restart both sites as a handoff mechanism. Fencing and a
  witnessed lease transition come first.

## Read-only baseline

Run from the cluster's administrative path. These commands intentionally avoid
secret values:

```bash
kubectl -n jmusicbot get deploy,pod,svc -o wide
kubectl -n jmusicbot get endpoints jmusicbot-health -o yaml
kubectl -n jmusicbot logs deploy/jmusicbot -c jmusicbot --since=15m \
  | grep -E 'lease|owner|witness|Discord|WebSocket|R2' | tail -50
kubectl -n jmusicbot get pod -l app.kubernetes.io/name=jmusicbot \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,CREATED:.metadata.creationTimestamp
```

Record UTC timestamps, site, pod image digest, readiness, restart count, and
whether the health Service has a ready endpoint in Issue #262 and parent issue
#191. A `Running` pod is not an owner proof; readiness and the witness log are
the relevant signals.

### R2 freshness baseline

Use the existing site-local `r2-sync` logs or the approved operator-side R2
listing command. Do not print credentials, signed URLs, or file contents:

```bash
kubectl -n jmusicbot logs deploy/jmusicbot -c r2-sync --since=30m \
  | grep -E 'sync|generation|timestamp|completed|skipped' | tail -50
```

Record the newest generation identifier, object timestamp, and observed lag as
metadata only. The acceptable RPO is the measured lag at the handoff boundary;
do not claim zero RPO from a successful sync log.

## Controlled Oracle -> home return

Use this only after an explicit maintenance checkpoint in Issue #262. The
normal owner must be stated before starting; current live state may differ.

1. Capture the read-only baseline above for both sites, including R2 freshness,
   lease epoch, and health endpoints.
2. Confirm Oracle is the sole effective owner and that no home Discord session
   exists. Observe Discord-side session identity and any R2 side-effect
   counters without changing state.
3. Stop or fence Oracle through the approved site-specific fence procedure.
   Confirm the old process is stopped, its health endpoint is not ready, and a
   stale lease renewal is rejected. Do not substitute `kubectl scale` for the
   fence check.
4. Verify the witness has expired/rejected the Oracle lease and record the next
   epoch. Do not manually edit R2 ownership markers.
5. Allow home to acquire the lease through its normal startup loop. Confirm the
   response site is home and the epoch increased; keep the opaque token out of
   logs and issue comments.
6. Confirm home health is ready (`200`) and Oracle remains not ready (`503`).
7. Observe one controlled Discord action and one R2 sync interval. Verify no
   duplicate Discord session or duplicate side effect occurred.
8. Record detection time, fencing time, acquisition time, route convergence,
   RTO, measured RPO, and rollback result. If any check fails, fence home and
   restore Oracle using the documented rollback path.

## Controlled home -> Oracle promotion

Use the same checkpoints in reverse:

1. Verify home is the sole owner and Oracle has current restored state.
2. Capture lease epoch, R2 generation/timestamp, egress/headroom baseline, and
   health status.
3. Fence home and prove stale renewal and stale side effects are rejected.
4. Wait for witness expiry, then allow Oracle to acquire the next epoch.
5. Verify Oracle readiness, one Discord session, and one R2 side-effect stream.
6. Route external traffic only after health and ownership checks pass.
7. Record RTO/RPO, duplicate checks, rollback, and return-home evidence.

## Validation and rollback

Run repository contracts before any rehearsal:

```bash
bash tests/jmusicbot-oracle-standby-test.sh
bash tests/jmusicbot-secret-restore-rbac-test.sh
kubectl kustomize docs/recovery/jmusicbot-oracle-standby >/dev/null
```

The standby overlay must retain one live process, `Recreate` strategy, witness
environment variables under the JMusicBot container, `/health` readiness,
`/live` liveness, and lease-marker-gated R2 sync. The secret-restore identity
must remain restricted to the named secrets and `get/update/patch` verbs.

Rollback is operator-controlled: fence the newly promoted owner, restore the
last known-good image/configuration through the dedicated workflow, verify the
old owner is still fenced, and only then reacquire the next witness epoch.
Never delete PVCs or R2 generations as part of a rehearsal.

## Current evidence boundary

Repository contracts and disposable promotion tests prove lease/fencing logic,
but do not prove a live Discord handoff, R2 freshness/RPO, egress capacity,
duplicate-side-effect exclusion, or measured RTO. Keep Issue #262 open until
those live acceptance checks are recorded. If a site is currently failing
closed while the other site owns the lease, treat that as production state and
do not restart either site without a planned handoff.
