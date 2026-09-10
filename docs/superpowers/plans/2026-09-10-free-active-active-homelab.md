# Free Active-Active Homelab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build free-tier active-active application availability across home and Oracle for non-Minecraft services, with PantryBot as the first complete implementation.

**Architecture:** Keep home and Oracle as independent k3s environments. Run application capacity in both sites, use PostgreSQL as PantryBot state and durable queue, and use leases/fencing/outbox processing to ensure one owner per external side effect.

**Tech Stack:** TypeScript/Node.js, PostgreSQL, Kubernetes/k3s, Patroni or equivalent PostgreSQL failover tooling, existing Cloudflare/Tailscale routing, R2 backups, GitHub Actions, GitHub Issues.

**Spec:** `docs/superpowers/specs/2026-09-10-free-active-active-homelab-design.md`

## Global Constraints

- Minecraft remains excluded and home-primary.
- Home and Oracle remain independent k3s control planes; do not stretch embedded etcd across WAN.
- PostgreSQL is the initial PantryBot durable queue; do not add NATS before measured need.
- Every external side effect requires an idempotency key, ownership lease, and fencing epoch.
- No paid service, resource resize, or quota increase without a separate cost decision.
- Secrets remain in Kubernetes/GitHub secret stores and never enter Git.
- GitHub Issues are the only task tracker.

### Task 1: Establish capacity and cost baseline

**Files:**
- Create: `docs/recovery/FREE-TIER-CAPACITY-BASELINE.md`
- Modify: `docs/recovery/RECOVERY-INVENTORY.md`
- Modify: GitHub Issues for each measured gap

**Interfaces:**
- Consumes: live resource measurements from home, Oracle, and GCP.
- Produces: recorded CPU, memory, disk, egress, backup growth, and free-tier limits that gate later deployment.

- [ ] Measure idle and peak CPU/memory/disk for Oracle and GCP while existing services run.
- [x] Measure home-to-Oracle latency and packet loss without modifying routing.
- [ ] Record GCP free-tier region/billing eligibility and monthly egress allowance.
- [ ] Record Oracle Always Free capacity, idle reclamation constraints, and current tenancy usage.
- [ ] Record R2 object count, total bytes, retention growth, and current backup writers.
- [ ] Define stop conditions: no new service placement when GCP has less than 20% memory headroom or when measured egress exceeds the free allowance.
- [ ] Validate the document with `git diff --check` and create GitHub follow-up issues for failed gates.

### Task 2: Create the PantryBot persistence/queue boundary

**Files:**
- Modify: `/home/chase/Downloads/pantry-bot/package.json`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/postgres.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/config.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/events.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/migrations/001_event_queue.sql`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/queue.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/outbox.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/ownership.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/schema.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/domainSchema.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/inventoryRepository.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/bossRepository.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/modRepository.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/runtime.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/README.md`
- Test: `/home/chase/Downloads/pantry-bot/src/platform/*.test.ts`

**Interfaces:**
- `openPostgres(config): Promise<Pool>` creates a bounded PostgreSQL pool.
- `enqueueEvent(tx, event): Promise<void>` inserts an idempotent event envelope.
- `claimWork(workerId, limit, leaseMs): Promise<WorkItem[]>` claims retryable work with row locks.
- `writeStateAndOutbox(tx, mutation, messages): Promise<void>` commits state and outbound messages atomically.
- `claimOutbox(target, ownerId, leaseMs): Promise<OutboxItem[]>` claims target-scoped outbound work.

- [x] Write tests for duplicate event IDs, retry visibility, dead-letter transition, per-entity ordering, and atomic outbox insertion.
- [x] Run the focused tests and confirm the implemented boundary passes.
- [x] Add the PostgreSQL client and schema initializer with connection timeout and pool limits.
- [x] Add explicit site, instance, role, lease, and polling configuration validation for split runtime deployments.
- [x] Implement queue and outbox SQL with unique event IDs, attempts, lease expiry, and terminal status.
- [x] Add tested worker and dispatcher execution loops that acknowledge only successful handlers.
- [x] Add abortable polling loops for independent worker and dispatcher processes.
- [x] Run the durable event/outbox claim, duplicate, and completion rehearsal against a disposable real PostgreSQL instance; cross-site promotion remains open.
- [x] Run focused tests, typecheck, and build.
- [ ] Keep SQLite adapters available only for migration tooling until production cutover is validated.

### Task 3: Add ownership and fencing

**Files:**
- Create: `/home/chase/Downloads/pantry-bot/src/platform/ownership.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/platform/ownership.test.ts`
- Modify: `/home/chase/k8s-homelab/docs/recovery/RECOVERY-INVENTORY.md`

**Interfaces:**
- `acquireLease(resource, ownerId, ttlMs): Promise<LeaseToken | null>` returns a monotonically increasing fencing epoch.
- `renewLease(token): Promise<boolean>` renews only the current epoch.
- `assertFence(token): Promise<void>` rejects stale owners before side effects.
- `releaseLease(token): Promise<void>` releases only the current owner.

- [ ] Test competing owners, lease expiry, renewal loss, stale epoch rejection, and clock-skew-safe server timestamps.
- [x] Implement ownership using PostgreSQL transactions and server-side time.
- [x] Add a process mode that refuses Twitch connection and dispatch when ownership is absent.
- [x] Add server-time fencing assertions and owner/epoch-scoped lease release; dispatcher tests prove stale ownership cannot send.
- [x] Add structured logs for acquisition, renewal loss, fencing rejection, and takeover.
- [x] Verify tests, typecheck, and build.

### Task 4: Split PantryBot UI/API and event-processing runtime roles

**Files:**
- Modify: `/home/chase/Downloads/pantry-bot/src/bot/index.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/gateway.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/worker.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/chatWorker.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/dispatcher.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/roles.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/api.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/ui.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/runtime/commandsSite.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/public-site/server.ts`
- Create: `/home/chase/Downloads/pantry-bot/src/public-site/public/index.html`
- Create: `/home/chase/Downloads/pantry-bot/Dockerfile.ui`
- Create: `/home/chase/Downloads/pantry-bot/Dockerfile.api`
- Create: `/home/chase/Downloads/pantry-bot/Dockerfile.public-site`
- Create: `/home/chase/Downloads/pantry-bot/k8s/commands-site.yaml`
- Modify: `/home/chase/Downloads/pantry-bot/package.json`
- Test: `/home/chase/Downloads/pantry-bot/src/runtime/*.test.ts`

**Interfaces:**
- `startGateway(deps): Promise<RuntimeHandle>` owns Twitch only while its lease is valid.
- `startWorker(deps): Promise<RuntimeHandle>` consumes events and commits state/outbox work.
- `startDispatcher(deps): Promise<RuntimeHandle>` sends only fenced, claimed outbox items.
- `startApi(deps): Promise<RuntimeHandle>` serves OAuth, authenticated mod commands, read models, overlay session setup, and readiness without owning Twitch.
- `startUi(deps): Promise<RuntimeHandle>` serves static mod-site and overlay assets without database or Twitch credentials.
- `startCommandsSite(deps): Promise<RuntimeHandle>` serves the public read-only command guide without OAuth, sessions, database writes, or Twitch credentials.

- [ ] Characterize current startup and command behavior with existing tests before extraction.
- [x] Add normalized event envelopes for chat, EventSub, and channel-point events.
- [x] Add a gateway ingress adapter that publishes normalized chat/EventSub events to the durable queue without executing commands or sending replies.
- [x] Add a real Twitch chat transport bridge and owned gateway lifecycle that detaches/disconnects on lease loss or shutdown.
- [x] Add deterministic normalized chat/EventSub envelopes with Twitch-ID deduplication and hashed fallback IDs.
- [x] Move the migrated command set behind durable queue consumption while preserving command registry behavior; remaining legacy command ports stay explicitly gated.
- [x] Add a tested chat-worker adapter that dispatches normalized chat events and writes replies as idempotent outbox messages; full production wiring remains gated on state migration.
- [x] Add a validated overlay outbox sender adapter for the fenced dispatcher; direct legacy broadcasts remain until all producers are migrated.
- [x] Route configured-runtime overlay broadcasts through the idempotent overlay outbox and start the owned overlay dispatcher; legacy mode remains direct.
- [x] Add independently startable worker and dispatcher role handles with abortable shutdown.
- [x] Add an owned dispatcher supervisor that acquires/renews a lease and fences every external send.
- [x] Move Twitch connection ownership into the gateway role and reconcile subscriptions after takeover.
- [x] Add Twitch chat/moderation and overlay outbox dispatcher boundaries; legacy producers remain during cutover.
- [x] Extract static mod-site and overlay assets into a separately built UI image with no secrets and no database access; leave API paths routed to the private API origin.
- [x] Extract the public commands guide into a separately routed commands-site image at `https://commands.greeniespantry.uk/` with no OAuth flow.
- [x] Add the read-only `/api/public/commands` manifest endpoint and an OAuth-free static commands-site image with two-replica Kubernetes deployment definition.
- [x] Extract private operator authentication, mod-command, read-model, and overlay session endpoints into an API image with no Twitch connection; shared signed moderator cookies are enabled for replicated API processes.
- [x] Add PostgreSQL-backed OAuth credential writes and moderator authorization reads to the API role; production cutover remains gated on a live encrypted-store rehearsal.
- [x] Extract API HTTP lifecycle into a separately testable runtime handle and remove direct `app.listen`/shutdown handling from the bot startup path.
- [x] Make PostgreSQL-backed mutating mod commands enqueue durable work instead of mutating state in the HTTP process; worker application, transactional audit/result recording, and giveaway announcement outbox coverage are complete.
- [ ] Keep UI/API available in both sites and expose `/livez`, `/readyz`, and ownership status independently.
- [x] Add `/livez` and `/readyz` API contracts and use them for the current API deployment probes; ownership status remains a separate role-runtime gate.
- [x] Expose `/ownership` separately from readiness so operators can observe dispatcher lease state and fencing epoch.
- [x] Add bounded runtime counters for durable event/outbox claims, completions, and failures without instance or user-cardinality labels.
- [x] Add a reversible ownership handoff rehearsal proving home ownership, Oracle rejection, takeover, and stale-fence rejection.
- [x] Run the full test suite, typecheck, and build; do not deploy until all pass.

### Task 5: PostgreSQL HA and independent-site manifests

**Files:**
- Create: `k8s/pantry-bot-ha/README.md`
- Create: `k8s/pantry-bot-ha/home/*.yaml`
- Create: `k8s/pantry-bot-ha/oracle/*.yaml`
- Create: `k8s/pantry-bot-ha/postgres/*.yaml`
- Create: `docs/recovery/PANTRYBOT-ACTIVE-ACTIVE-RUNBOOK.md`
- Create: `docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md`

**Interfaces:**
- Both clusters receive equivalent PantryBot role deployments with site identity configuration.
- Database failover produces one primary epoch and rejects stale writers.
- Public routing sends traffic only to an application-ready origin.

- [ ] Choose Patroni/etcd or another free coordination implementation only after Task 1 capacity results pass.
- [x] Deploy a disposable non-production PostgreSQL rehearsal with generated credentials and explicit cleanup; persistent cross-site storage remains open.
- [x] Run the live event/outbox rehearsal on both home/chasebot and Oracle arm64; cross-site replication and promotion remain open.
- [x] Validate the asynchronous replication path and record its trade-off: disposable home-to-Oracle logical replication observed a home write on Oracle; it is one-way and async, so RPO remains replication lag and automatic promotion is still open.
- [ ] Add home and Oracle deployments for gateway, workers, dispatcher, and HTTP roles.
- [ ] Add anti-affinity and resource limits that fit both sites.
- [ ] Add free health-aware routing and document TTL/detection behavior.
- [x] Document the public commands/moderator/OAuth hostname and path-routing contract.
- [x] Write the PantryBot active-active promotion, fencing, rollback, and free-cost runbook; live database promotion evidence remains open.
- [ ] Run cross-site non-production database failover and stale-database-writer fencing tests; application-side promotion, lease takeover, and stale-token rejection are proven, but PostgreSQL promotion remains open.

### Task 6: Migrate state and cut over PantryBot

**Files:**
- Create: `/home/chase/Downloads/pantry-bot/scripts/export-sqlite.ts`
- Create: `/home/chase/Downloads/pantry-bot/scripts/import-postgres.ts`
- Create: `docs/recovery/PANTRYBOT-MIGRATION-RUNBOOK.md`
- Modify: existing PantryBot deployment manifests

**Interfaces:**
- Export/import preserves users, inventory, boss state, rewards, auth metadata, and app state without exposing tokens.
- Cutover is reversible by scaling old runtime to zero and retaining the SQLite backup.

- [x] Write the migration, rollback, and failover-evidence runbook before any production cutover.

- [ ] Export a consistent SQLite snapshot and verify row counts/checksums.
- [x] Add a redacted SQLite snapshot exporter with deterministic per-table row counts and SHA-256 checksums.
- [x] Add an isolated PostgreSQL rehearsal importer that validates snapshot checksums and stores redacted rows without token columns.
- [x] Add the PostgreSQL target schema for PantryBot domain state alongside the queue/lease schema; repository cutover remains separate.
- [x] Add a foreign-key-ordered rehearsal import into the real domain tables, with auth-token tables skipped for secret restoration.
- [x] Add the first async PostgreSQL repository adapter for users/inventory with transactional overlay outbox writes and duplicate-event protection.
- [x] Make inventory removal transactional and idempotent with an overlay outbox event, and validate the inventory event type in the overlay dispatcher.
- [x] Add the async PostgreSQL boss/contribution repository with row-locked damage application and active-state fencing.
- [x] Couple PostgreSQL boss creation/damage state updates to idempotent overlay outbox messages in the same transaction.
- [x] Add the async PostgreSQL custom-command repository used by the moderator/API surface.
- [x] Allow the moderator API to use the PostgreSQL custom-command repository while retaining SQLite fallback during migration.
- [x] Add PostgreSQL AutoMod phrase/strike state and optional moderator-API wiring with SQLite fallback.
- [x] Make the real PantryBot startup opt into the PostgreSQL application store when site/instance/runtime-role configuration is present, while preserving legacy SQLite startup.
- [x] Add PostgreSQL timed-message state and route moderator timed-message CRUD through it when the application store is enabled.
- [x] Add PostgreSQL giveaway state, deduplicated entries, and single-winner draw ownership; route moderator giveaway CRUD/draw through it when enabled.
- [x] Add encrypted PostgreSQL Twitch credential storage, shared token refresh, and a reversible SQLite-to-PostgreSQL auth import.
- [x] Switch the configured Twitch runtime's timed-message, AutoMod, and giveaway paths to the shared PostgreSQL repositories, retaining SQLite fallback.
- [x] Route the configured live Twitch callback's AutoMod and giveaway reads/writes through shared PostgreSQL, retaining SQLite fallback.
- [ ] Import into an isolated PostgreSQL database and compare all non-secret state.
- [ ] Run synthetic commands and verify event IDs, state mutation, outbox rows, and overlay updates.
- [ ] Run a controlled home-to-Oracle ownership handoff.
- [ ] Keep the old deployment recoverable until the failover rehearsal passes.
- [ ] Record RTO, RPO, duplicate behavior, and rollback result in GitHub Issue #191.

### Task 7: Apply the portable pattern to non-Minecraft services

**Files:**
- Modify: service-specific manifests and runbooks only after service issue approval.
- Create: `docs/recovery/SERVICE-ACTIVE-ACTIVE-MATRIX.md`

**Interfaces:**
- Each service declares state authority, side effects, ownership model, backup, routing, and failure test.

- [x] Classify Opsbot, Operations, Authentik, observability, and CI tunnel as stateless, stateful, or externally side-effecting; record per-service promotion gates in `docs/recovery/SERVICE-FAILOVER-GATES.md`.
- [ ] Run replicas in Oracle for stateless services that pass capacity and credential portability gates.
- [ ] Keep stateful services on independent recovery paths until database promotion and fencing are proven.
- [ ] Keep JMusicBot home-primary/Oracle-standby unless egress measurements prove otherwise.
- [ ] Leave Minecraft unchanged and explicitly mark it excluded in the matrix.
- [x] Create the service matrix and one GitHub Issue per remaining service gate; link the gates to the architecture issue.

### Task 8: Rehearse and enable automatic failover

**Files:**
- Create: `scripts/pantrybot-failover-rehearsal.sh`
- Create: PantryBot worktree `scripts/rehearse-cross-site-promotion.ts`
- Create: `docs/recovery/FAILURE-REHEARSAL-RESULTS.md`
- Modify: GitHub Issue #191

**Interfaces:**
- Rehearsal scripts are read-only or reversible by default and require explicit target selection for destructive simulation.
- Results include timestamps, failure injected, detection time, fencing proof, promotion time, routing time, RTO, RPO, and rollback.

- [ ] Test process crash and node loss.
- [ ] Test whole-home outage simulation by disabling home ownership and routing in a non-production window.
- [ ] Test asymmetric partition and prove the stale dispatcher cannot send.
- [ ] Test PostgreSQL promotion and queue recovery.
- [ ] Test Oracle failure and controlled return to home.
- [ ] Enable automatic production failover only after all required evidence is attached to GitHub.

The PantryBot candidate now includes a provider-neutral failover state machine
that enforces witness authority, old-writer fencing, database promotion,
database/application readiness, and routing order. It does not enable
failover or replace the missing provider adapters.
