# Pantry Bot home promotion state

This document records the production state reached during the September 2026
maintenance window. GitHub issue `k8s-homelab-191` is the evidence log for the
live changes and remaining gates.

## Current authority

- The failover witness resource is `pantry:postgres`.
- Home (`MinecraftMachine`) holds the current authority lease after the stale
  Oracle writer was fenced.
- Home PostgreSQL is `postgres-authority-home-failback-0` in namespace
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
- A controlled rehearsal reached the guarded Oracle promotion path, but the
  deployed promoter stopped before database mutation because its validation
  query used absent container environment variables and its local-fence hook
  still named an obsolete StatefulSet. No Oracle promotion was claimed from
  that attempt; the home writer and runtime were restored and re-verified.
- The previous failback PVC was verified to contain the authoritative home
  primary data after the interrupted rehearsal. Its StatefulSet init guard was
  restored to a fail-closed primary-data/standby-data check, the pod was
  recreated without touching the PVC, and the live pod is labeled `primary`.
  The service now has a Ready endpoint and reports
  `pg_is_in_recovery()=f`; the original standby seeding manifest remains
  standby-only for future fresh-PVC failback runs.
- The fenced Oracle replica has also been reseeded from the home primary over
  Tailscale `100.84.89.87:30432`. It is Ready and streaming with slot
  `oracle_from_home`; the home primary reports that slot active. Oracle
  application roles remain stopped while this standby is validated.
- The tracked Oracle adapter now accepts separate pod/service namespaces,
  PostgreSQL data directory, and manually managed Endpoints (`--manual-endpoint`)
  for the live `canada-standby-prep` topology. Its focused test suite passes
  (22 tests), and the corrected source plus a topology-pinned disabled systemd
  drop-in are installed on Oracle. A fixed-identity composite old-writer fence
  now dry-runs successfully against both home and Canada. Automatic service
  enablement remains gated on the local service-check hook proving writable
  authority and on a full live rehearsal using that composite fence; the
  verified one-shot rehearsal remains the production evidence.
- The deployed site-neutral promoter had a readiness bug in an earlier copy
  (`_kubectl` was referenced instead of its configured closure); the live copy
  was backed up, corrected, and py_compile-validated. The tracked source and
  deployed copy now match.
