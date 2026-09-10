# Non-Minecraft active-active service matrix

Status: architecture classification, 2026-09-10. This matrix is a gate for
placement and failover work; a service is not considered highly available just
because a second Deployment exists.

| Service | Class | State authority | External side effects / ownership | Home capacity | Oracle capacity | Current decision and gate |
|---|---|---|---|---|---|---|
| PantryBot | Stateful, event-processing | PostgreSQL target; SQLite migration in progress | Twitch chat/EventSub, overlay, moderation; PostgreSQL lease/fencing per target | Active application site | Active application site | Reference implementation. Finish repository conversion, live PostgreSQL rehearsal, routing and failover evidence. See PantryBot #147 and homelab #191. |
| Cartwise | Stateful web/API | Local PostgreSQL PVC; web code is a home hostPath | User/API writes and background jobs; idempotency and job ownership not yet classified | Primary | None | Single home writer. Remove hostPath, add backup/restore or promotion protocol, and classify side effects before Oracle capacity. See homelab #205. |
| Opsbot | Stateful operational controller | Bot-local state plus Kubernetes API/RCON access | Discord messages, pod control, Minecraft/RCON commands; one owner per command stream | Primary | Standby/recovery only initially | Keep home-primary while command idempotency, credentials, and Oracle API access are proven. See homelab #200. |
| Operations dashboard | Stateful web/API | SQLite backup to R2; Authentik session dependency | Browser/API mutations and audit writes; no autonomous external writer | Primary | Restore-capable standby | Do not run active-active SQLite writers. Migrate state or prove controlled promotion, emergency local auth, and R2 restore. See homelab #201. |
| Authentik + PostgreSQL | Stateful identity | PostgreSQL PVC/dump | Identity/session issuance; duplicate active writers are unsafe | Primary | Isolated restore/controlled promotion | Keep independent from PantryBot HA. Validate database promotion, secret reconstruction, emergency access, and Authentik startup. See homelab #198/#202. |
| Observability (Prometheus/Loki/Grafana) | Stateful history, stateless collectors | Local PVCs plus external Grafana/monitoring paths | Alert delivery and dashboards; collectors can duplicate, alert evaluation needs dedupe | Primary collectors | Lightweight collectors/observer | Prefer redundant collection and external observation over cross-site replicated history. Bound storage and validate alert continuity. See homelab #203. |
| CI deployment tunnel | Stateless connector with privileged API credential | Cloudflare/GitHub workflow configuration | Kubernetes deployment mutations; workflow identity must be scoped | Connector capacity | Connector capacity if independently credentialed | Run connectors in both sites only after Access route, credential, and split-brain deployment tests. See homelab #204. |
| JMusicBot | Stateful external bot | R2-backed configuration/state mirror | Discord voice/music messages; Oracle egress is constrained | Home-primary | Oracle standby/controlled promotion | Do not active-active by default. Promote only with writer fencing and measured egress allowance. Existing migration runbook applies. |
| Minecraft | Explicitly excluded | Local world plus local/offsite archives | Minecraft server/RCON; home-primary by requirement | Primary only | No workload | Do not alter placement, storage, or writer model in this project. |

## Placement rules

- Stateless HTTP/UI capacity may run in both sites after image, credential,
  routing, and readiness checks pass.
- Any service that writes SQLite or local PVC state remains single-writer until
  its state authority and promotion protocol are proven.
- Any Discord, Twitch, RCON, Kubernetes mutation, or notification sender needs
  a target-specific ownership/fencing contract and an idempotency policy.
- Oracle is not assumed to have unlimited egress; JMusicBot and other media
  workloads remain gated by measured usage.
- Minecraft is not a fallback capacity target and must not be modified to make
  other services highly available.

## Evidence required for promotion

Each service issue must record state freshness, credential portability, routing
behavior, RTO/RPO, duplicate-side-effect behavior, rollback, and the exact
failure test that was run. A healthy pod or tunnel connector alone is not
promotion evidence.
