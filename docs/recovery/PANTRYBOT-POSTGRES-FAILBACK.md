# PantryBot PostgreSQL controlled failback

This is the return path after Oracle has been promoted. It is intentionally
controlled: automatic failover is useful during an outage, but automatic
failback would create an avoidable second writer during recovery.

The procedure uses a new home PVC (`postgres-authority-home-failback-0`). It
never overwrites the old home PVC in place. The stable `postgres-authority`
Service is not switched until the new home copy is caught up, Oracle is
fenced, and home has acquired a newer `pantry:postgres` witness epoch.

## Preconditions

All of these must be true before starting:

1. Oracle PostgreSQL is writable and is the only promoted database writer.
2. The Oracle application roles are scaled to zero or otherwise stopped.
3. Home's old PostgreSQL writer domain is fenced. A stopped API or a stale
   Kubernetes label is not sufficient proof.
4. The reverse transport is active:
   `192.168.40.200:25433` reaches Oracle's local PostgreSQL port through GCP.
5. The home failback Secret exists out-of-band with the same database and
   replication credentials as Oracle, and its `PRIMARY_SLOT_NAME` is unique.
6. There is enough ChaseBot disk for a second 8 GiB local-path claim during
   the transition. The old PVC is retained until verification completes.

The current home root filesystem is tight. Check it before applying the
manifest; do not delete container images or production data as an ad-hoc
space fix.

## Ordered transition

The order is part of the safety contract:

1. Stop Oracle gateway, worker, dispatcher, and write-capable API roles.
2. Apply `pantrybot-postgres-standby-home-failback.yaml` to the home cluster.
3. Wait for `postgres-authority-home-failback-0` to complete `pg_basebackup`
   and report `pg_is_in_recovery() = true`.
4. Verify the home standby replay LSN has caught up to the Oracle primary's
   current WAL flush LSN. Record both LSNs and the observed lag.
5. Fence Oracle's Kubernetes writer domain and wait for the shared witness
   lease to expire. Do not acquire the home lease before this fence.
6. Acquire `pantry:postgres` as `home`; reject the transition if the returned
   epoch is not newer than the Oracle promotion epoch.
7. Promote the home failback pod with `pg_ctl promote`, then verify
   `pg_is_in_recovery() = false` and a test transaction succeeds.
8. Patch the stable `postgres-authority` Service selector to the failback pod,
   point `PANTRY_DATABASE_URL` back to that Service, and restart the private
   API/site and overlay deployments.
9. Verify application readiness and queue/outbox recovery before enabling home
   gateway, workers, and dispatcher.
10. Only after those checks, scale Oracle stateless roles back up in standby
    mode and recreate the Oracle physical standby from home.

The state-machine policy is implemented in
`observability/failover-witness/failback_controller.py`. Its tests enforce
that a non-primary Oracle, an unfenced home, an uncaught-up standby, a missing
home lease, or a lost renewal cannot result in home promotion.

## Evidence boundary

Repository tests, manifest validation, and controller dry-runs verify policy
contracts only; they do not prove that a live promotion or failback is safe.
The maintenance record must include all of the following before this gate is
closed:

- one confirmed PostgreSQL writer and an externally fenced old writer whose
  stale writes are rejected;
- a newer witness/fencing epoch acquired only after the old writer is fenced;
- successful standby promotion and a committed test transaction;
- endpoint and application cutover with stale-writer rejection verified;
- measured failover RTO and replication/data-loss RPO; and
- controlled failback with replication direction and the final sole writer
  verified afterward.

## Current evidence and remaining gate

The reverse encrypted transport and disposable `pg_basebackup -R` path are
already proven. The new failback target manifest and ordering controller are
repository-ready and client-side dry-run clean. The production PVC cutover
has not been run yet: it requires the controlled maintenance window and the
ChaseBot host's writerless k3s maintenance mode. Keep PantryBot's
gateway/worker/dispatcher at zero on Oracle until the first complete
promotion, routing, stale-writer rejection, and return-home rehearsal is
recorded in GitHub Issues #147 and #191.

## Known hazard: fence scope includes home-return standby

**Regression guard:** `test_failback_fence_scope_document_hazard_with_home_return` in
`observability/failover-witness/test_pantry_postgres_fence.py`.

The normal home fence (`fence-pantry-postgres.sh`) targets three StatefulSets:
- `postgres-authority` (home writer)
- `postgres-authority-home-failback` (temporary failback target)
- `postgres-authority-home-return` (prepared standby)

**Safety constraint:** The normal home fence must not be invoked during Oracle-to-home failback. Fencing Oracle requires a separate Oracle fence adapter before acquiring the home lease, while preserving `postgres-authority-home-return` on home so that standby replication back from the restored home primary is not destroyed.

**Resolution path:**
1. Failback must never invoke `fence-pantry-postgres.sh` directly.
2. The failback sequence requires a separate Oracle fence adapter before acquiring the home lease.
3. Any home-side fence adapter used during or after failback must preserve `postgres-authority-home-return`.

**Verification:** Run `pytest observability/failover-witness/test_pantry_postgres_fence.py`
to confirm the hazard is documented and the fence script behavior is unchanged.
