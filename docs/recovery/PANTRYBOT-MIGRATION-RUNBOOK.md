# PantryBot SQLite-to-PostgreSQL migration runbook

Status: preparation only. Do not use this runbook for production cutover until
the PostgreSQL rehearsal and fencing evidence are attached to GitHub Issues
#147 and #191.

## Invariants

- Minecraft is out of scope and must not be changed by this migration.
- The SQLite file remains a recoverable rollback artifact until failover and
  replay validation pass.
- Twitch access tokens and refresh tokens are secrets. Export manifests must
  contain identifiers and checksums only, never token values.
- No two PantryBot sites may own the same Twitch or outbound target lease.
- A migration is not successful merely because PostgreSQL accepts rows; the
  application must prove command, outbox, overlay, and replay behavior.

## 1. Freeze and snapshot

1. Create a maintenance window and stop mutating command traffic.
2. Confirm the current pod/process has stopped writing SQLite and Litestream
   has uploaded the final WAL/checkpoint.
3. Copy the SQLite database and its WAL to a restricted recovery directory.
4. Record file hashes, SQLite integrity-check output, and row counts for every
   non-secret table.
5. Export auth metadata as redacted records containing Twitch user IDs,
   usernames, scopes, expiry, and a checksum of the token-bearing row. Never
   print or upload token columns.

The snapshot is the rollback source. Do not delete or overwrite it during the
rehearsal.

## 2. Import rehearsal

1. Provision an isolated PostgreSQL database with encrypted credentials and
   the PantryBot platform schema.
2. Import users, inventory, boss state/contributions/rewards, app state,
   duels, engagement, community, moderation, custom commands, timed messages,
   giveaways, and redaction-safe auth metadata.
3. Preserve primary keys and timestamps where the target schema supports them.
4. Compare per-table counts and deterministic non-secret checksums against the
   frozen SQLite snapshot.
5. Store the comparison output as an Issue comment or attached artifact; do
   not treat an unrecorded local comparison as evidence.

## 3. Behavioral rehearsal

In the isolated database, run synthetic cases for:

- a viewer chat command and duplicate delivery;
- two commands for the same entity, proving ordered processing;
- a mutating mod command;
- an EventSub/channel-point event;
- a committed state change plus outbox row;
- dispatcher retry, dead-letter, and ambiguous sender response;
- overlay reconnect and state resynchronization;
- lease takeover and rejection of the stale owner's send.

Record event IDs, outbox idempotency keys, final statuses, and overlay results.

## 4. Controlled cutover

1. Scale the old runtime down and confirm its Twitch connection is closed.
2. Acquire the first PostgreSQL ownership epoch from the home site.
3. Start home API/UI, gateway, workers, and dispatchers against PostgreSQL.
4. Start Oracle capacity without Twitch ownership and confirm it can claim
   work only when the home lease is expired or deliberately released.
5. Verify public commands, private mod console, OAuth callback, overlay
   reconnect, Twitch chat, EventSub, and health endpoints.
6. Keep the SQLite snapshot and old image available until the rollback window
   expires and Issue #191 records the result.

## 5. Rollback

If behavioral checks fail:

1. Stop new PostgreSQL writers and release/fence the current ownership epoch.
2. Stop the new gateway and dispatchers; verify no stale process can send.
3. Restore the SQLite snapshot to a new path, never over the original artifact.
4. Start the previous image in home-primary mode and verify Twitch and overlay
   behavior.
5. Record the failure, data freshness, duplicate/loss behavior, and elapsed
   recovery time in GitHub before retrying.

## 6. Failover evidence required before automation

Attach results to #147/#191 for:

- home node failure and whole-home outage;
- asymmetric partition with stale-dispatcher fencing;
- PostgreSQL promotion and queue recovery;
- Oracle failure and controlled return to home;
- measured RTO/RPO and duplicate-delivery policy;
- free-tier CPU, memory, storage, egress, and backup-growth checks.

Automatic failback remains disabled until repeated controlled rehearsals prove
that ownership, database authority, routing, and application readiness change
as one operation.
