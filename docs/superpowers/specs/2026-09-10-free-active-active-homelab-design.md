# Free Active-Active Homelab Design

**Status:** approved design; implementation begins with GitHub-tracked, reversible work

**Scope:** all internet-facing and recoverable homelab services except Minecraft. Minecraft remains home-primary and is not included in active-active migration.

## Goal

Provide automatic service continuity across the home environment and Oracle without adding paid infrastructure, while avoiding WAN-spanning Kubernetes control-plane consensus. PantryBot is the reference implementation and must use this flow:

```text
Twitch gateway -> durable event queue -> horizontally scalable workers
                 -> transactional outbox -> outbound dispatcher -> Twitch/overlays/etc.
```

Both sites run service capacity continuously. External side effects are protected with distributed ownership and fencing so active-active compute does not become duplicate-active Twitch writers.

## Constraints and non-goals

- Use existing home, Oracle, and GCP resources first; no paid Cloudflare Load Balancing, managed database, or new server is required for the initial design.
- Do not place Oracle back into the home k3s control plane. Home and Oracle remain independent k3s environments.
- Do not make Minecraft active-active or move its writer to Oracle.
- Do not claim zero cost until free-tier eligibility, egress, backup growth, and quota usage are measured.
- Do not use SQLite/Litestream as the authoritative multi-site PantryBot state store.
- Do not promise exactly-once delivery from Twitch. Twitch and overlay side effects require idempotency, reconciliation, and an explicit duplicate-versus-loss policy.

## Topology

### Home

`minecraftmachine` remains the home k3s control plane and continues to host Minecraft. Non-Minecraft workloads may run on the home cluster, with `chasebot` used for worker/replica capacity and external service placement where practical. No migration in this project changes Minecraft scheduling or storage.

### Oracle

`pantry-bot-oracle` remains an independent ARM64 k3s server. It runs the same continuously available non-Minecraft service capacity as home. Services with external side effects use a target-specific lease and fencing epoch so both sites can be running without both sites performing the same side effect. Oracle egress is a capacity constraint to measure, not a reason to leave a service powered off or classify it as standby.

### GCP

`discordmusicbot` remains an external observer and coordination participant. Its approximately 1 GiB memory budget is a hard capacity gate; it must not become a general workload node. Any coordination service placed there must be measured for memory, disk, and network usage before production reliance.

## PantryBot data flow

### Runtime separation

PantryBot is split into independently scalable surfaces. Browser-facing UI must not share a process lifecycle with Twitch connectivity or event processing:

- **Static UI pods:** serve the mod commands site, overlay shell, and versioned browser assets. They are stateless, cacheable, and run in both sites.
- **Public commands-site pods:** serve the viewer-facing command guide at `https://commands.greeniespantry.uk/`. This surface is read-only, requires no OAuth, and contains no operator controls or Twitch credentials.
- **API/mod-command pods:** handle private operator/moderator authentication, authenticated mod commands, read models, overlay session setup, and readiness. They do not connect to Twitch directly. Mutating mod commands become durable queue events, so API replicas can run in both sites.
- **Overlay delivery:** overlay clients connect to an API/overlay endpoint; updates come from committed events or read models rather than process-local state. Reconnect and state resynchronization are preferred over site affinity.
- **Event-processing pods:** gateway, workers, and dispatcher remain separate from UI/API pods and are governed by ownership and fencing rules.

The public commands site, private mod API, overlay delivery, and event-processing roles have separate container targets, Kubernetes Deployments, readiness checks, resource limits, and rollout controls. A public UI rollout, operator-authentication fault, or Twitch OAuth maintenance operation must not restart the Twitch gateway. The public commands hostname must not route through the OAuth hostname.

### Gateway

Gateway replicas run in both sites. A durable lease identifies the current owner for each Twitch channel/account. Only the owner maintains the authoritative IRC/EventSub connection and publishes normalized events. The standby gateway is live and credentialed but does not own the external connection until it acquires the lease.

Each normalized event has a stable event ID, source, received timestamp, channel/entity key, payload, and processing state. The gateway persists the event before treating it as accepted. EventSub reconnect and subscription reconciliation are mandatory after ownership changes.

### Durable queue

PostgreSQL is the initial queue to minimize cost and operational surface area. A work table supports leases, `FOR UPDATE SKIP LOCKED`, retries, dead-letter state, visibility timeouts, and per-entity ordering. NATS JetStream remains an optional later replacement if measured throughput or retention needs justify another component.

### Workers

Workers run in both sites and horizontally scale. They consume queue entries without owning Twitch connections. Each worker uses an idempotency key and entity ownership/ordering rules. Game-state changes and outbound work are committed in one PostgreSQL transaction.

### Outbox and dispatcher

Every external command is written to a transactional outbox in the same transaction as the state change. Dispatcher replicas run in both sites. A target-specific lease and fencing epoch prevent an old site from sending after failover. Dispatch attempts record request IDs, response status, retry eligibility, and reconciliation status.

### Database authority

The application is active-active; the database is not an uncontrolled multi-primary database. PostgreSQL uses one primary per fencing epoch with automated, fenced promotion. Patroni and a quorum-capable coordination layer are evaluated across home, Oracle, and GCP only after capacity and network tests pass. If cross-site synchronous replication is too expensive in latency or bandwidth, the project must record an explicit RPO before using asynchronous replication.

## Other services

- Stateless web/API/operations components should run replicas in both sites when their credentials, ingress, and storage are portable.
- Public viewer URLs should be short and descriptive: `commands.greeniespantry.uk` for the command guide, with private operator authentication on a separate hostname/path.
- Observability must retain an external observer on GCP or Oracle and must not depend only on the home cluster.
- Authentik and its database remain a separate availability workstream. Emergency SSH and monitoring access must not require Authentik.
- JMusicBot runs a live process in both sites; a Discord-session ownership lease permits exactly one external voice writer at a time. Oracle egress is measured and bounded, but Oracle is not a powered-off standby.
- Minecraft remains excluded from this active-active design.

## Free routing strategy

Use the existing Cloudflare/Tailscale capabilities and self-hosted health-aware routing first. Public endpoints must have a home origin and an Oracle origin, with health checks that test application readiness rather than only tunnel connector liveness. DNS or proxy failover must be documented with its detection and TTL behavior. Cloudflare Load Balancing is not part of the free baseline; it may be considered only through a separate cost approval.

## Failure and safety model

Required tests before enabling automatic production failover:

1. Home application/node failure while Oracle is healthy.
2. Whole-home WAN or power loss.
3. Asymmetric partition where the old site still reaches Twitch.
4. Database primary failure and promotion.
5. Paused or stale process that attempts to resume after fencing.
6. Duplicate delivery and ambiguous Twitch API response.
7. Oracle failure and controlled return to home.

Automatic recovery is considered proven only when detection, fencing, ownership transfer, routing, data freshness, and application readiness are measured together. Return of ownership is controlled initially; automatic return is deferred until repeated rehearsals show it is safe. Both sites remain active capacity throughout the ownership transition.

## Cost guardrails

The baseline uses existing machines, open-source PostgreSQL/Patroni or equivalent, existing R2, existing tunnels, and GitHub Issues. The project pauses before any paid service, resource resize, or quota increase. Monthly checks record GCP free-tier usage, Oracle Always Free usage, Cloudflare/R2 usage, and egress.

## Acceptance criteria

- PantryBot has gateway, queue, worker, outbox, and dispatcher boundaries represented in code and deployment manifests.
- Home and Oracle have continuously running capacity, with no duplicate Twitch owner during normal operation or tested failover.
- A home outage promotes Oracle without manual database repair and with a measured RTO/RPO.
- Old-site fencing is demonstrated during an asymmetric partition.
- Public operations and overlay routes can reach the surviving site using the free routing strategy.
- At least one additional non-Minecraft service follows the same independent-site, portable-state, externally-observed pattern.
- All remaining gaps are GitHub Issues; no local tracker is used.
