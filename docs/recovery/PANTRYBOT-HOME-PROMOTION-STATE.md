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
- The replication NodePort selector is temporarily pointed at the current
  primary `home-return` pod so Oracle can be reseeded; the standby manifest
  switches that selector back when a new return standby is prepared.
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

The sequence has been exercised in production in both directions. During the
Oracle promotion rehearsal, the composite old-writer fence completed at
16:30:02 UTC, Oracle promoted at 16:30:04 UTC, and the Oracle service check
passed with all mutating roles Ready. During controlled failback, Oracle was
fenced, home reacquired a newer witness epoch, the fresh home-return PVC was
seeded from Oracle and promoted, and the stable home endpoint was restored.
Home PostgreSQL now reports `pg_is_in_recovery()=f` and
`transaction_read_only=off`; all home runtime deployments and both commands
site replicas are Ready.

## Remaining gates

- The worker-only component rollout reached Kubernetes but immutable GHCR image
  pulls returned HTTP 403. Cached home workers remain healthy; the failed
  ReplicaSet is removed and the worker Deployment is paused. A dedicated
  least-privilege `read:packages` credential and one successful rollout remain
  open.
- Oracle application roles remain stopped in standby posture after reseeding;
  the standby reports `pg_is_in_recovery()=t` and caught-up receive/replay LSNs.
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
