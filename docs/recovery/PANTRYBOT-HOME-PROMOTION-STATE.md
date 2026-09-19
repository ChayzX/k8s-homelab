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
- The home readiness probe expects `pg_is_in_recovery() = f`; the former
  standby-only seeding init container is not used after promotion.

## Recovery validation

The controlled promotion sequence is:

1. Verify the old Oracle writer is fenced (StatefulSet scaled to zero, PVC
   retained, writer port closed).
2. Acquire/retain the witness authority lease for home.
3. Promote the home PostgreSQL pod and label it primary.
4. Switch the service selector and platform database URL.
5. Start only the home runtime components whose immutable images are available.

The sequence has been exercised in production. Home PostgreSQL returned
`pg_is_in_recovery()=f` and `transaction_read_only=off`; an in-cluster API
smoke test returned `{"status":"ready"}`. Home API, private site, overlay,
two workers, dispatcher, gateway, and both public commands-site replicas are
currently Ready. The commands-site `/ready` and `/api/public/commands` checks
pass.

## Remaining gates

- GHCR package authentication is still unavailable from home. Recovery uses
  verified immutable images imported from the Oracle containerd cache or a
  locally built public-site target; credentials were not copied or invented.
- Oracle application roles remain stopped while its old writer is fenced.
- Controlled PostgreSQL failback to Oracle, followed by replication-direction
  verification, is still required before declaring database HA complete.
- A prepared failback standby now runs on ChaseBot from a fresh PVC and
  streams from the home primary (`pg_is_in_recovery()=t`, WAL slot
  `pantry_home_failback`). Its service has no endpoint while it is standby;
  only the replication NodePort selects the current home primary.
- The deployed site-neutral promoter had a readiness bug in an earlier copy
  (`_kubectl` was referenced instead of its configured closure); the live copy
  was backed up, corrected, and py_compile-validated. The source-level fix
  must be carried into the tracked promoter implementation before the next
  promotion rehearsal.
