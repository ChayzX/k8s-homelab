# Non-Minecraft failover gates

This is the implementation gate for applying the PantryBot pattern to the
other services. A second pod is not active-active evidence when the service has
state or an external side effect. Minecraft is intentionally excluded.

## Opsbot

Current model: home-primary, Oracle standby/recovery.

- Keep exactly one Discord gateway owner for the bot identity.
- Keep exactly one Kubernetes mutation owner for `/deploy`, `/mc`, and other
  mutating commands.
- A promoted instance must acquire a site-scoped lease before connecting to
  Discord or executing a mutation. A stale instance must stop its gateway and
  reject mutation commands after lease loss.
- `/pods status` and health-only endpoints may run on standby capacity, but
  read-only duplication does not authorize duplicate Discord gateway sessions.
- Promotion evidence: Discord login, lease acquisition, Kubernetes API access,
  one authorized mutation, stale-owner rejection, and controlled return home.

Until a shared external lease authority is available and tested, do not raise
the Deployment above one replica or run an Oracle copy against the live Discord
identity.

## Operations dashboard

Current model: home-primary, Oracle restore-capable standby.

- Do not run two SQLite writers or share the SQLite file over the WAN.
- Keep the current R2/versioned backup and isolated restore path.
- Before promotion, restore to a local writable database, validate `/readyz`,
  verify emergency access without Authentik, and fence the home writer.
- Active-active HTTP replicas become valid only after writes move to a shared
  transactional authority and session/audit state is portable.

## Authentik and its PostgreSQL

Current model: independent identity recovery workstream.

- Keep Authentik and its database single-writer until PostgreSQL promotion,
  secret reconstruction, and session behavior are rehearsed together.
- Never make PantryBot, monitoring, or emergency SSH depend on the Authentik
  primary being reachable.
- Promotion evidence must include a non-production login, provider/secret
  restoration, database consistency, and rollback to the original writer.

## Observability

Current model: active collectors where useful, primary history with external
observation.

- Node exporters and log collectors may run in both sites because their data
  is append-only telemetry.
- Prometheus/Loki/Grafana history remains independently backed up; do not
  pretend two local PVCs are one replicated history store.
- Alert rules need deduplication or one designated evaluator. UptimeRobot/GCP
  public checks remain the external failure detector.
- Promotion evidence is alert delivery while the home cluster and Authentik
  are unavailable, not merely a healthy collector pod.

## CI deployment tunnel

Current model: one active deployment authority per target cluster.

- Duplicate connectors are acceptable only when Cloudflare Access routes and
  workflow credentials are independently scoped to the intended cluster.
- A workflow must select one target and record the target/site identity; do
  not let two tunnels race the same rollout.
- Promotion evidence is a controlled deployment to a non-production target,
  stale-target rejection, and restoration of the original connector.

## JMusicBot

Current model: home-primary, Oracle standby/recovery.

- Discord voice and music activity are external side effects and must remain
  single-owner.
- Promote only after Oracle egress and audio-session behavior are measured;
  the Oracle free-tier allowance is not assumed to cover sustained media.
- Preserve the existing R2-backed configuration/state recovery path.

## Common evidence record

Each service issue must attach the target site, owner/epoch or promotion lock,
failure injection, detection time, promotion time, RTO, RPO, duplicate-side-
effect result, rollback result, and the exact command/output used for proof.
