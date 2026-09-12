# Non-Minecraft failover gates

This is the implementation gate for applying the PantryBot pattern to the
other services. The target is active-active application capacity with a
single-writer fenced database or state authority. A second pod, a second site,
or a successful readiness probe is not active-active evidence when the service
has state or an external side effect. Minecraft is intentionally excluded.

For every service below, “promotion” means: fence the old writer, establish
exclusive write authority at the new site, validate state freshness, start
side-effecting roles, verify routing, and record rollback. These are required
gates, not claims that the service currently satisfies them.

## Gate vocabulary

- **Capacity gate:** the image, resources, credentials, readiness, and routing
  work at both sites without requiring the other site's cluster.
- **State gate:** the state authority has a measured freshness/RPO, restore or
  promotion procedure, and rejection of stale writers.
- **Side-effect gate:** every Discord, Twitch, RCON, Kubernetes, notification,
  or background-job lane has one owner, a fencing epoch/lock, idempotency, and
  observable duplicate suppression.
- **Failure gate:** a recorded loss/partition test proves detection,
  promotion, RTO/RPO, routing convergence, and rollback.

## Opsbot

Current model: active application capacity with one fenced external-side-effect
owner; home-primary, Oracle standby/recovery until all gates pass.

- Capacity gate: deploy a home and Oracle replica with portable Discord,
  GitHub, Kubernetes, and RCON credentials; verify health/read-only commands
  while the replica is not the owner.
- State gate: identify bot-local state and command/job state, define its backup
  freshness, and ensure a promoted replica cannot use stale local state to
  repeat a mutation.
- Side-effect gate: keep exactly one Discord gateway owner and exactly one
  Kubernetes/RCON mutation owner per command stream. The owner must acquire a
  site-scoped lease before connecting or mutating; lease loss must disconnect
  Discord and reject mutations.
- Failure gate: prove Discord login, lease acquisition, Kubernetes API access,
  one authorized mutation, stale-owner rejection after partition/lease loss,
  duplicate suppression, and controlled return home with timestamps and RTO.

Until a shared external lease authority is available and tested, do not raise
the Deployment above one replica or run an Oracle copy against the live Discord
identity.

## Operations dashboard

Current model: active HTTP/application capacity with a single SQLite writer;
home-primary, Oracle restore-capable standby.

- Capacity gate: run the dashboard/UI in Oracle with no dependency on the home
  SQLite file, verify `/readyz`, and route only read-safe traffic until
  promotion is authorized.
- State gate: retain versioned R2 backups, record generation freshness, restore
  a local writable SQLite copy, and define audit/session portability. The
  isolated restore acceptance record must include the artifact checksum,
  target boundary, image digest, SQLite `integrity_check`, expected table
  count, `/livez`, and `/readyz`; it must also state that no production PVC,
  Service, ingress, or scheduled writer was enabled. Never share the SQLite
  file over the WAN.
- Emergency-access gate: while Authentik and LDAP are unavailable in the
  isolated recovery target, prove the owner recovery path can reach that
  target without an Authentik/LDAP session. Record only the path type,
  target boundary, authentication mechanism name, sanitized endpoint/status
  output, and cleanup result; never record credential values. Health endpoints
  alone do not pass this gate. If the path cannot perform an authorized
  dashboard read or mutation, record the limitation and leave this gate open.
- Side-effect gate: fence the home writer before enabling Oracle writes; verify
  browser/API mutations and audit writes are single-owner and idempotent.
- Failure gate: simulate home loss, restore the selected generation, validate
  the emergency-access gate without Authentik, route to Oracle, measure RTO/RPO,
  and roll back to home. A shared transactional database is required before
  two writable dashboard sites are called active-active. The existing
  Operations rehearsal proves isolated restore/readiness only; it does not
  prove emergency access, promotion, fencing, routing, or rollback.

## Authentik and its PostgreSQL

Current model: independent identity service with active application capacity
only after database promotion is proven; home-primary, Oracle isolated
restore/controlled-promotion capacity.

- Capacity gate: start Authentik against the intended local PostgreSQL endpoint
  at each site, with resource limits and no dependency on PantryBot or the
  other site's cluster.
- State gate: restore a known PostgreSQL dump/WAL position, verify schema and
  consistency, promote exactly one PostgreSQL writer, and reject writes from
  the old primary after fencing. Do not use the Authentik database as PantryBot
  state authority.
- Credential gate: reconstruct database, signing, provider, LDAP/outpost, and
  bootstrap secrets from the approved secret stores; verify emergency SSH and
  monitoring remain usable without Authentik.
- Failure gate: perform a non-production login, provider/session issuance,
  database consistency check, old-writer rejection, routing validation, and
  rollback to the original writer. Backup restore alone does not pass this
  gate.

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

Current model: active application capacity with one Discord voice/music owner;
home-primary, Oracle standby/recovery.

- Capacity gate: deploy an Oracle warm standby with pinned image, health
  endpoint, portable credentials, and bounded CPU/memory/disk resources; keep
  it disconnected from Discord voice until promotion.
- State gate: restore the latest approved R2 generation, verify config/token
  integrity and notifier-state requirements, and record freshness/RPO.
- Side-effect gate: Discord voice/music activity must remain single-owner;
  fence the home instance and verify the old instance cannot reconnect or
  issue duplicate work before Oracle starts.
- Egress gate: measure Oracle bandwidth, sustained audio behavior, reconnects,
  and monthly free-tier headroom under representative load. The Oracle free
  allowance is not assumed to cover sustained media.
- Failure gate: perform controlled promotion, Discord duplicate-work/session
  check, health/routing validation, RTO/RPO capture, and rollback. Preserve the
  existing R2-backed recovery path throughout.

## Explicit exclusions

Cartwise is outside this project. Do not add Oracle placement, failover gates,
or production HA changes for it here; any future Cartwise work requires a
separate scope decision and tracking issue.

## Common evidence record

Each service issue must attach the target site, owner/epoch or promotion lock,
failure injection, detection time, promotion time, RTO, RPO, duplicate-side-
effect result, rollback result, and the exact command/output used for proof.
