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
  stale `postgres-authority-standby-reseed-0` candidate retained promoted
  primary state and remains scaled to zero. A new
  `postgres-authority-standby-reseed-v2-0` candidate was freshly reseeded with
  `pg_basebackup -R`, a verified `standby.signal`, and the
  `pantry_oracle_standby` slot; it reports `pg_is_in_recovery()=t` and is
  streaming from Home. Neither separate service is an application writer
  endpoint; the home service remains authoritative.
- **Active reseed endpoint (live override):** Oracle's reseed candidate reaches
  the current home writer through `100.84.89.87:30432`, the
  `pantry-bot/postgres-authority-replication` NodePort. While home is the
  writer, that live Service selector is explicitly
  `app.kubernetes.io/name=pantry-postgres-authority-home-return,
  pantrybot.postgres/role=primary`. This is an operational override of the
  future-failback manifest in
  `pantrybot-postgres-home-return.yaml`, whose selector intentionally remains
  `role=standby`. Do **not** apply that manifest over the live Service while
  the home writer is active: doing so would silently remove the replication
  endpoint from the writable pod. Reconcile the selector and capture fresh
  Endpoints/EndpointSlices before changing replication direction.
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

The isolated/application-level ownership rehearsal exercised both directions
and proved the application lease/fencing sequence. A controlled live Oracle
promotion and return-home failback were exercised on 2026-09-20: the composite
old-writer fence stopped Home and Canada, Oracle promoted the streaming
candidate, application roles and routes were brought up, Oracle was then
fenced, and a fresh Home failback PVC was seeded from Oracle and promoted.
The current live snapshot is `postgres-authority-home-failback-0` as the
writable primary; the Oracle reseed StatefulSet is scaled to zero with no
endpoint. Home PostgreSQL reports `pg_is_in_recovery()=f` and
`transaction_read_only=off`; all nine Home runtime/tunnel deployments are
Ready and external commands/OAuth/mods/overlay checks returned 200/302/200/302.

## Remaining gates

- The worker-only component rollout and public-site rollout now pull immutable
  GHCR images successfully using the least-privilege `read:packages` secret.
  The worker Deployment remains paused as an intentional rollout-control
  decision; cached workers are healthy.
- Oracle application roles remain stopped. The stale promoted PostgreSQL
  candidate is fenced with no endpoint, while the v2 standby is read-only
  recovery capacity. The Home failback PVC was created only after a
  fresh Oracle dump (`oracle-pre-failback-20260920T132458Z.dump`, SHA-256
  `c94eabb1b95a5d0431d19331efccbdbd24ced52582e52eea83a45415fa4a6f68`) and
  caught up from Oracle over the verified reverse transport.
- The corrected promoter and composite home+Canada fence have passed source-level
  contract tests, dry-run checks, and isolated/controller rehearsal coverage.
  Live Oracle promotion, service cutover, route publication, and return-home
  failback are now proven for this rehearsal. The promoter remains disabled and
  automatic failover is not enabled.
- The stale home-return PVC was deleted only after backup and replacement state
  were verified. The canonical standby manifest was reapplied, the replication
  credential was synchronized from the Oracle secret, and the fresh PVC
  streamed from Oracle before home promotion.
- A safety probe found the original retained Oracle reseed PVC had lost standby
  state and reported `pg_is_in_recovery()=f`; it was immediately scaled to
  zero and remains fenced. The v2 candidate was then freshly reseeded from the
  current home primary over the verified transport and now reports
  `pg_is_in_recovery()=t` with an active `pantry_oracle_standby` slot. The home
  primary still reports the sole application authority endpoint.
- The tracked Oracle adapter now accepts separate pod/service namespaces,
  PostgreSQL data directory, and manually managed Endpoints (`--manual-endpoint`)
  for the live `postgres-authority-standby-reseed-v2` topology. Its focused test suite passes
  (23 tests), and the corrected source plus a topology-pinned disabled systemd
  drop-in are installed on Oracle. A fixed-identity composite old-writer fence
  has verified its transport and dry-run contracts for both home and Canada; a
  live Home-only fence rehearsal was completed on 2026-09-20, followed by a
  live cross-site promotion/failback rehearsal: the adapter
  removed all named home PostgreSQL endpoints in 1.972s, and the writable
  home-return StatefulSet was restored in 16.853s with runtime readiness
  recovered. Explicit stale-write transaction rejection and source-marked
  RTO/RPO measurements remain open gates; endpoint removal and old-writer
  fencing were verified.
  Automatic service enablement remains disabled pending repeated
  failure-domain testing and external-side-effect/RTO-RPO evidence.
- Live candidate inspection on 2026-09-20 confirmed `PGDATA=/var/lib/postgresql/data`;
  the disabled promoter example, regression test, and installed drop-in were
  corrected from the obsolete `/var/lib/postgresql/data/pgdata` path. The
  service was daemon-reloaded and remains inactive.
- The deployed site-neutral promoter had a readiness bug in an earlier copy
  (`_kubectl` was referenced instead of its configured closure); the live copy
  was backed up, corrected, and py_compile-validated. The tracked source and
  deployed copy now match.
