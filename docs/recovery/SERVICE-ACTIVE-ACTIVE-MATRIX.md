# Non-Minecraft active-active service matrix

Status: live placement review, 2026-09-13. This matrix is a gate for
placement and failover work; a service is not considered highly available just
because a second Deployment exists.

## Architecture decision

The target model is **active-active application capacity with a single-writer
fenced database**. Home and Oracle may both run stateless HTTP/UI capacity,
workers, collectors, and recovery-ready replicas. They must not independently
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
| PantryBot | Stateful, event-processing | PostgreSQL authority; Oracle currently writable, home physical standby | Twitch chat/EventSub, overlay, moderation; PostgreSQL lease/fencing per target | Active application capacity; standby database | Active application capacity; current database authority | Reference implementation. Split roles, durable queue/outbox, public routing, and cross-site application capacity are live. Remaining gates are Minecraft-safe source fencing, controlled return-home, and full failure evidence. See PantryBot #147 and homelab #191. |
| Cartwise | Explicitly out of scope | Not evaluated in this project | Not evaluated | No changes | No changes | Excluded by project scope; do not modify, migrate, or add HA work here. |
| Opsbot | Stateful operational controller | Bot-local state plus Kubernetes API/RCON access | Discord messages, pod control, Minecraft/RCON commands; one owner per command stream | Active capacity, one side-effect owner | Standby-capable process with lease-denied readiness, no duplicate owner | Keep one fenced Discord/mutation owner. The Oracle standby health path is proven; promotion/return-home evidence remains a gate. See homelab #200. |
| Operations dashboard | Stateful web/API | SQLite backup to R2; Authentik session dependency | Browser/API mutations and audit writes; no autonomous external writer | Active capacity, single SQLite writer | Restore-capable application capacity | Do not run active-active SQLite writers. First prove emergency access, restore freshness, local writable promotion, writer fencing, and routing; only then consider a shared transactional authority. See homelab #201. |
| Authentik + PostgreSQL | Stateful identity | PostgreSQL PVC/dump | Identity/session issuance; duplicate active writers are unsafe | Active primary capacity | Isolated restore/controlled-promotion capacity | PantryBot OAuth provider/application, Greenie binding, embedded-outpost assignment, and public OAuth challenge are live; keep independent from PantryBot HA. Prove PostgreSQL promotion and old-writer fencing, secret/provider reconstruction, emergency access independent of Authentik, credentialed non-production login, and rollback. See homelab #168/#198/#202. |
| Observability (Prometheus/Loki/Grafana) | Stateful history, stateless collectors | Local PVCs plus Grafana Cloud long-lived metrics/log paths | Alert delivery and dashboards; collectors can duplicate, alert evaluation needs dedupe | Local collector/history plus Cloud write path | Oracle-labeled Prometheus collector plus Cloud write path | Both collectors are live with zero remote-write failures; queue convergence, Cloud Explore data validation, and alert continuity remain open. See homelab #203. |
| CI deployment tunnel | Stateless connector with privileged API credential | Cloudflare/GitHub workflow configuration | Kubernetes deployment mutations; workflow identity must be scoped | Connector capacity | Connector capacity if independently credentialed | Run connectors in both sites only after Access route, credential, and split-brain deployment tests. See homelab #204. |
| JMusicBot | Stateful external bot | R2-backed configuration/state mirror | Discord voice/music messages; Oracle egress is constrained | Active home capacity, one writer | Warm standby/controlled promotion capacity | Do not run active-active Discord voice writers. Promote only after R2 freshness/config restore, Discord session fencing, duplicate-work checks, startup/health proof, and measured Oracle egress/audio behavior. Existing migration runbook applies. |
| Minecraft | Explicitly excluded | Local world plus local/offsite archives | Minecraft server/RCON; home-primary by requirement | Primary only | No workload | Do not alter placement, storage, or writer model in this project. |

## Placement rules

- Stateless HTTP/UI capacity may run in both sites after image, credential,
  routing, and readiness checks pass; this is application capacity, not proof
  that its database or external integrations are active-active.
- Any service that writes SQLite or local PVC state remains single-writer until
  its state authority and promotion protocol are proven.
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
