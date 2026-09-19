# Pantry Bot home promotion state

This document records the production state reached during the September 2026
maintenance window. GitHub issue `k8s-homelab-191` is the evidence log for the
live changes and remaining gates.

## Current authority

- The failover witness resource is `pantry:postgres`.
- Home (`MinecraftMachine`) holds the current authority lease after the stale
  Oracle writer was fenced.
- Home PostgreSQL is `postgres-authority-home-return-0` in namespace
  `pantry-bot`; it is labeled `pantrybot.postgres/role=primary`.
- The home service selector requires both the home PostgreSQL name and the
  `primary` role label.
- `PANTRY_DATABASE_URL` points at the in-cluster home service.
- The replication NodePort selector is pointed at the current primary
  home-return pod for Oracle reseeding. The original
  postgres-authority-standby StatefulSet remains scaled to zero and its
  retained PVC is deliberately untouched. A fresh, separately named
  postgres-authority-standby-reseed-0 candidate now runs on Oracle with a
  new PVC, standby.signal, the unique pantry_oracle_standby slot, and a
  streaming WAL receiver. Its separate service is not an application writer
  endpoint; the home service remains authoritative.
- The home readiness probe expects `pg_is_in_recovery() = f`; the former
  standby-only seeding init container is not used after promotion.

## Recovery validation

The controlled promotion sequence is:

1. Verify the old Oracle writer is fenced (StatefulSet scaled to zero, PVC
   retained or replaced only after backup verification, writer port closed).
2. Acquire/retain the witness authority lease for home.
3. Promote the home PostgreSQL pod and label it primary.
4. Switch the service selector and platform database URL.
5. Start only the home runtime components whose immutable images are available.

The controlled promotion/failback rehearsal exercised both directions and
proved the application lease/fencing sequence. The current live snapshot
(2026-09-19) is home postgres-authority-home-return-0 as the writable primary;
the canonical Oracle postgres-authority-standby StatefulSet is still 0/0,
while the separately named reseed candidate is 1/1, reports
pg_is_in_recovery()=t, and reports pg_stat_wal_receiver.status=streaming.
Home PostgreSQL reports pg_is_in_recovery()=f and
transaction_read_only=off; home runtime deployments and both commands-site
replicas are Ready.

## Remaining gates

- The worker-only component rollout and public-site rollout now pull immutable
  GHCR images successfully using the least-privilege `read:packages` secret.
  The worker Deployment remains paused as an intentional rollout-control
  decision; cached workers are healthy.
- Oracle application roles remain stopped in standby posture. The canonical
  Pantry PostgreSQL standby remains scaled to zero with its old PVC retained;
  the fresh reseed candidate is continuously streaming from home. Before
  replacing the canonical StatefulSet, capture repeated receive/replay LSN
  freshness, verify service/secret wiring, and perform the documented
  old-writer fencing checks.
- The corrected promoter completed the guarded Oracle promotion path with the
  composite home+Canada fence, replication gate, database promotion, service
  check, and route publication. The promoter remains disabled after the
  controlled rehearsal; automatic failover is not enabled.
- The stale home-return PVC was deleted only after backup and replacement state
  were verified. The canonical standby manifest was reapplied, the replication
  credential was synchronized from the Oracle secret, and the fresh PVC
  streamed from Oracle before home promotion.
- Oracle was then reseeded from the current home primary over Tailscale
  `100.84.89.87:30432`; it now reports `pg_is_in_recovery()=t` with receive and
  replay LSNs caught up. The home primary reports the replication endpoint.
- The tracked Oracle adapter now accepts separate pod/service namespaces,
  PostgreSQL data directory, and manually managed Endpoints (`--manual-endpoint`)
  for the live `canada-standby-prep` topology. Its focused test suite passes
  (23 tests), and the corrected source plus a topology-pinned disabled systemd
  drop-in are installed on Oracle. A fixed-identity composite old-writer fence
  completed successfully against both home and Canada in the controlled
  rehearsal. Automatic service enablement remains disabled pending repeated
  failure-domain testing and external-side-effect/RTO-RPO evidence.
- The deployed site-neutral promoter had a readiness bug in an earlier copy
  (`_kubectl` was referenced instead of its configured closure); the live copy
  was backed up, corrected, and py_compile-validated. The tracked source and
  deployed copy now match.
