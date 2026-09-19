# Non-Minecraft active-active service matrix

Status: architecture classification, 2026-09-11. This matrix is a gate for
placement and failover work; a service is not considered highly available just
because a second Deployment exists.

## Architecture decision

The target model is **active-active application capacity with a single-writer
fenced database where the underlying protocol requires one writer**. Home and
Oracle both run continuously available service capacity: HTTP/UI capacity,
workers, collectors, and side-effect-capable processes. They must not independently
write the same state authority or perform the same external side effect unless
the service has a target-specific ownership lease, monotonically increasing
fencing epoch, idempotency/reconciliation behavior, and a tested promotion
procedure. Database promotion is a separate gate from application replica
readiness; two running application sites do not imply multi-primary database
writes.

The matrix records the intended placement, not completed implementation. A
row marked active-active capacity remains gated until state promotion,
side-effect fencing, routing, and failure evidence are attached to its linked
GitHub issue.

| Service | Class | State authority | External side effects / ownership | Home capacity | Oracle capacity | Current decision and gate |
|---|---|---|---|---|---|---|
| PantryBot | Stateful, event-processing | PostgreSQL target; SQLite retained only for rollback/migration | Twitch chat/EventSub, overlay, moderation; PostgreSQL lease/fencing per target | Active application capacity | Active application capacity | Database promotion, old-writer fencing, controlled failback, and Oracle standby reseed are production-verified. Remaining gates are GHCR-authenticated component rollout, SQLite/domain-state reconciliation, provider-side external-effect semantics, public failover routing, and broader site/failure evidence. See PantryBot #147 and homelab #191. |
| Cartwise | Out of scope | Not evaluated | Not evaluated | Not part of this project | Not part of this project | Preserve existing deployment; do not include Cartwise in the active-active rollout or completion gate. |
| Opsbot | Stateful operational controller | Bot-local state plus Kubernetes API/RCON access | Discord messages, pod control, Minecraft/RCON commands; one owner per command stream | Active capacity, one side-effect owner | Active capacity, one side-effect owner | Run a live Oracle replica continuously. Keep one fenced Discord/mutation owner per command stream; prove credential/API scope, lease loss, stale-owner rejection, and controlled ownership return. See homelab #200 and homelab #191. |
| Operations dashboard | Stateful web/API | SQLite backup to R2; Authentik session dependency | Browser/API mutations and audit writes; no autonomous external writer | Active capacity, current writer | Active read/API capacity with restored local SQLite and mutations disabled | Oracle read/API capacity, pod self-healing, restore integrity, and readiness are verified. Public-route failover, Authentik-independent emergency access, writable promotion/fencing, and a shared transactional authority remain open; do not run two SQLite writers. See homelab #201. |
| Authentik + PostgreSQL | Stateful identity with an active-active application tier | Independent PostgreSQL authority per site with one fenced writer per epoch | Identity/session issuance; both sites serve application capacity, while database promotion and epoch fencing prevent stale writers | Active server/worker/LDAP capacity; current PantryBot authority is home | Active server/worker/LDAP capacity; Oracle is the read-only PantryBot standby | Authentik-specific provider/session parity, routing, emergency access, promotion/fencing, and rollback remain open; do not infer their completion from PantryBot evidence. See homelab #198/#202. |
| Observability (Prometheus/Loki/Grafana) | Stateful history, stateless collectors | Local PVCs plus external Grafana/monitoring paths | Alert delivery and dashboards; collectors can duplicate, alert evaluation needs dedupe | Primary collectors | Lightweight collectors/observer | Prefer redundant collection and external observation over cross-site replicated history. Bound storage and validate alert continuity. See homelab #203. |
| CI deployment tunnel | Stateless connector with privileged API credential | Cloudflare/GitHub workflow configuration | Kubernetes deployment mutations; workflow identity must be scoped | Connector capacity | Connector capacity if independently credentialed | Run connectors in both sites only after Access route, credential, and split-brain deployment tests. See homelab #204. |
| JMusicBot | Stateful external bot | R2-backed configuration/state mirror | Discord voice/music messages; resource-scoped witness lease and fencing; Oracle egress is constrained | Active capacity, lease-controlled writer | Active capacity, lease-controlled non-owner | Both sites run the process, but only the `jmusicbot` witness lease holder may open Discord or sync R2 state. Ownership transfer remains gated on witness provisioning, R2 freshness/config restore, duplicate-work checks, startup/health proof, and measured Oracle egress/audio behavior. |
| Minecraft | Explicitly excluded | Local world plus local/offsite archives | Minecraft server/RCON; home-primary by requirement | Primary only | No workload | Do not alter placement, storage, or writer model in this project. |

## Placement rules

- Stateless HTTP/UI capacity may run in both sites after image, credential,
  routing, and readiness checks pass; this is application capacity, not proof
  that its database or external integrations are active-active.
- Any service that writes SQLite or local PVC state remains single-writer until
  its state authority and promotion protocol are proven; this does not prevent
  an active-active application tier from serving both sites.
- Any Discord, Twitch, RCON, Kubernetes mutation, or notification sender needs
  a target-specific ownership/fencing contract and an idempotency policy.
- Background jobs are external-side-effect producers too: they require durable
  job identity, retry/reconciliation rules, and one fenced owner per job lane.
- Oracle is not assumed to have unlimited egress; JMusicBot and other media
  workloads remain gated by measured usage.
- Minecraft is not a fallback capacity target and must not be modified to make
  other services highly available.

## Evidence required for promotion

Each service issue must record state freshness, credential portability, routing
behavior, RTO/RPO, duplicate-side-effect behavior, rollback, and the exact
failure test that was run. A healthy pod or tunnel connector alone is not
promotion evidence.
