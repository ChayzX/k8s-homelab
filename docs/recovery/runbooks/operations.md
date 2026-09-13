# Operations dashboard disaster recovery SOP

## Current topology and authority

Operations is maintained in `/home/chase/Operations-ios-app`. The main workload
is `operations-web` in namespace `operations`, with SQLite at `/data/operations.db`
on the `operations-data` PVC. The daily backup is
`k8s-homelab/scripts/operations-db-backup.sh`, which uses SQLite's online
backup API and uploads under the documented R2 recovery prefix.

Oracle has a continuously running recovery/read-only overlay in
`deploy/oracle-active-active-readonly/`. It is not a second SQLite writer:
`OPERATIONS_MUTATION_MODE=disabled` removes mutation capability while the
read/API health surface remains available. The zero-replica manifest in
`docs/recovery/operations-oracle-standby/replicas-zero.yaml` is retained for
an emergency state where even read capacity must be stopped. The Operations CI publish workflow builds a
multi-architecture image for `linux/amd64` and `linux/arm64`, so image
architecture is no longer the Oracle blocker. Oracle remains zero-replica until
the writable restore, Authentik-independent emergency access, SQLite writer
fencing, routing, and rollback gates pass.

## Tracking and evidence

Record Operations restore, access, writer-fencing, route, and failback evidence
in [homelab #201](https://github.com/ChayzX/k8s-homelab/issues/201). Record
cross-service capacity and RTO/RPO evidence in
[homelab #191](https://github.com/ChayzX/k8s-homelab/issues/191). Issue #201 is
the authority for whether Oracle remains read-only or has passed a controlled
writable-promotion gate.

## Normal health check

```bash
kubectl -n operations get deploy,pods,svc,pvc -o wide
kubectl -n operations describe deployment/operations-web
kubectl -n operations logs deployment/operations-web --since=30m --tail=200
kubectl -n operations port-forward service/operations-web 18080:80
curl --max-time 10 -fsS http://127.0.0.1:18080/livez
curl --max-time 10 -fsS http://127.0.0.1:18080/readyz
```

Do not use the port-forward as evidence that Cloudflare/Auth routing works.
Check the protected external route separately after the local service is
healthy.

## Read-only triage

Before restarting, scaling, mounting, restoring, or routing, confirm context
and capture sanitized state:

```bash
kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
kubectl -n operations get deploy,pods,svc,endpoints,pvc -o wide
kubectl -n operations get events --sort-by=.lastTimestamp | tail -80
kubectl -n operations logs deployment/operations-web --since=30m --tail=200
```

Inspect backup names/checksums and PVC metadata without credentials. Run SQLite
`PRAGMA integrity_check` against a copied/isolated database, not the live file
during writes. A local probe or port-forward does not prove route or writer
safety.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Deployment unavailable, probe failure, restart/OOM count | Events, previous logs, image/config, and probe responses; do not touch the SQLite file while the pod is writing | `kubectl -n operations rollout restart deployment/operations-web`; roll back a bad image with `rollout undo`. | `/livez` and `/readyz` 200, recent logs clean, SQLite remains mounted, and external protected route works. |
| Home node/site loss | Node condition, site management loss, or Operations pod/PVC unavailable | Identify whether the local-path PVC's node is lost; do not force mount it elsewhere or infer site loss from one route | Do not scale Oracle writable merely because the home pod is gone. Use the latest R2 backup to restore a local Oracle copy only after writer fencing and emergency-access gates. | Restored copy passes `PRAGMA integrity_check`, expected table count, probes, and a documented Authentik-independent operator action. |
| WAN partition | External Operations route fails while one or both sites may still be reachable | Check local Oracle target, home writer, Cloudflare, Authentik, and external monitor independently; identify backup generation | Keep Oracle read-only/zero replicas unless a controlled promotion fences home and authorizes writes. Never share SQLite over WAN or promote on timeout alone. | Route reaches the intended site, no two writers, RTO/RPO recorded, and one synthetic read/write is audited only after promotion. |
| Oracle outage | Oracle recovery pod/node unavailable | Home SQLite writer and backup job are authoritative; inspect Oracle only when it returns | Keep home as the sole writer. Recreate the Oracle recovery target from a fresh backup, respecting AMD64 placement. | Home continues serving; Oracle restore/readiness evidence is refreshed before use. |
| Split brain/duplicate writer | Two `operations-web` pods with writable state, duplicated audit mutations, or conflicting generations | Inspect deployment replicas, PVC, backup generation, and mutation mode; stop the newer/uncertain writer before any write | Fence the old writer, select one SQLite generation, and rebuild the other from backup. Scaling alone is not a proof of fencing. | Exactly one writable SQLite file, audit records are reconciled, and stale target cannot mutate. |
| SQLite/PVC corruption | `integrity_check` failure, I/O errors, locked/WAL errors, PVC mount failure | Stop the app; preserve the PVC and WAL; run integrity checks against a copy, not the live file | Restore a selected `operations-*.db.gz` through the documented isolated procedure. Do not overwrite production until checksum/table/probe evidence is reviewed. | `PRAGMA integrity_check` returns `ok`, expected table count, `/livez`, `/readyz`, and backup checksum recorded in #201. |
| Secret/certificate/Auth failure | Authentik 401/5xx, Access rejection, terminal or alert ingest failure, TLS errors | Check Secret names/key names and sanitized config; inspect Authentik and Cloudflare separately | Reconstruct the specific Secret from approved escrow; restart Operations only after the value is present. Roll back the Secret version if checks regress. | Protected login, permitted dashboard action, audit write, alert ingest, and host-command security checks pass without recording values. |
| Routing/tunnel failure | Local probes pass but external hostname fails/502 | Compare Service endpoints, Cloudflare origin, Authentik route, and certificate; avoid changing all layers at once | Restore the exact Operations hostname/origin in the shared tunnel and roll back a bad route/config version. | External URL, Authentik redirect, and an authorized read work; direct port-forward is not sufficient. |
| Resource exhaustion | OOM/eviction, PVC full, SQLite lock latency, host pressure | `kubectl top`, events, PVC usage, application metrics/logs; do not add a second SQLite writer | Reduce nonessential polling/history, increase only measured limits, or roll back a resource-heavy image. Prune only documented disposable data. | Stable probes, mutation latency normal, backup completes, and R2 retention/free-tier budget remains valid. |
| Rollback/failback | Oracle promotion or new image fails, or home returns | Preserve backup generation, file checksum, mutation mode, route version, and audit tail | For app/image, `rollout undo`. For site return, stop/fence Oracle writer, restore/catch up home, verify home writable, then route home and keep Oracle recovery-ready. | One writer, audit continuity, home probes/route healthy, Oracle copy marked non-writer, and #201 updated. |

## Restore and promotion procedure

1. Select a backup by timestamp, size, and SHA-256; never use an unverified
   partial file.
2. Restore into an isolated namespace/emptyDir first. Run SQLite integrity and
   table-count checks, then `/livez`/`/readyz`.
3. Prove the emergency-access path while Authentik/LDAP are unavailable. A
   health endpoint alone does not pass this gate.
4. For a real promotion, fence the home writer, apply the Oracle recovery
   overlay, enable exactly one writable local copy, and then change routing.
5. Record a synthetic read and one authorized mutation, including the audit
   record and duplicate behavior.

Promotion is gated, not automatic. Before enabling Oracle writes, prove
independent emergency access, select and checksum a complete backup, place the
workload on compatible AMD64 capacity, fence home, verify stale-writer
rejection, and validate the route. Failback stops/fences Oracle, restores or
catches up home, validates writability and audit continuity, then routes home
while leaving Oracle non-writable.

## Verification checklist

Record in #201 and #191: backup generation/checksum and RPO; integrity and
table-count checks; exactly one writable SQLite copy; stale-writer rejection;
`/livez` and `/readyz`; Authentik-independent access; protected route; one
authorized mutation with its audit row; and rollback/failback evidence.

## Known gates

- Current evidence is isolated restore/readiness, not emergency access,
  promotion, routing, writer fencing, RTO/RPO, or rollback.
- A shared transactional database is required before Operations can be called
  two-site writable active-active.
- The current image's AMD64-only constraint applies to Oracle.
