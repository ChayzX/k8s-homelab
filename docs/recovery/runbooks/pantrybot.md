# PantryBot disaster recovery and failover SOP

## Current topology and authority

PantryBot’s split application is in `/home/chase/Downloads/pantry-bot/k8s/ha/`:

- `pantry-commands-site` is the public, OAuth-free viewer UI/API.
- `pantry-private-site` serves the moderator UI.
- `pantry-private-api` owns OAuth/session and moderator mutations.
- `pantry-twitch-gateway` owns the Twitch/EventSub ingress only while it holds
  the fenced gateway lease.
- `pantry-chat-worker` horizontally consumes the PostgreSQL-backed durable
  queue and writes the transactional outbox.
- `pantry-twitch-dispatcher` owns Twitch chat/moderation/channel-point sends
  through independent leases.
- `pantry-overlay-delivery` owns overlay delivery through its own lease.

Home and Oracle have site overlays in `k8s/ha/home/` and `k8s/ha/oracle/`.
The neutral witness and node-local relays are defined in
`observability/failover-witness/`. PostgreSQL is a single-writer authority;
the Oracle standby is not writable until the database-fencing gate passes.
The legacy SQLite deployment in `k8s/deployment.yaml` and homelab
`pantry-bot/40-deployment.yaml` is not the HA path and must not be started as a
second writer during an incident.

## Current recovery state (2026-09-12)

The live system is in an Oracle-primary recovery window, not normal
home-primary operation:

- `pantry-bot-platform/PANTRY_DATABASE_URL` points to the Oracle database
  endpoint; do not print the Secret value while checking this.
- Oracle `postgres-authority-standby-0` reports `pg_is_in_recovery() = false`.
- Home `postgres-authority-home-return-0` reports `pg_is_in_recovery() = true`
  and is intentionally not selected by the production application endpoint.
- Oracle currently owns the Twitch ingress, Twitch outbound, and overlay lease
  rows. Home application capacity may remain Ready, but it must not acquire a
  second external-side-effect owner.

Do not switch the application Secret, promote the home standby, or reseed
Oracle from home until the current Oracle writer is fenced, the home standby
is verified current, and the controlled return-home sequence below has an
explicit maintenance record in GitHub Issue #147 or #191. A healthy pod or a
successful readiness probe is not a fencing proof.

## Normal health check

```bash
kubectl -n pantry-bot get deploy,pods,svc -o wide
kubectl -n pantry-bot get endpoints pantry-commands-site pantry-private-site pantry-private-api pantry-overlay
kubectl -n pantry-bot get pods -l 'app.kubernetes.io/component in (gateway,worker,dispatcher)'
kubectl -n pantry-bot logs deploy/pantry-twitch-gateway --since=15m --tail=100
kubectl -n pantry-bot logs deploy/pantry-twitch-dispatcher --since=15m --tail=100
```

External route checks, with no secrets or cookies:

```bash
curl --max-time 15 -fsS https://commands.greeniespantry.uk/api/public/commands
curl --max-time 15 -fsS -o /dev/null -w '%{http_code}\n' https://oauth.greeniespantry.uk/login/broadcaster
curl --max-time 15 -fsS -o /dev/null -w '%{http_code}\n' https://mods.greeniespantry.uk/mod/
curl --max-time 15 -fsS -I https://overlay.greeniespantry.uk/
```

The required Cloudflare origins are recorded in
`docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md`; tunnel tokens are in Secrets, not
Git. Confirm endpoints before blaming Cloudflare: an origin with no endpoints
produces a tunnel error even when the connector itself is healthy.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Deployment available replicas, pod phase, restart count, `/ready` or role state | `describe`, previous logs, events, image digest, and witness/lease messages; do not delete a healthy peer | Restart or roll back the affected stateless role only: `kubectl -n pantry-bot rollout restart deployment/<role>`; wait for `rollout status`. A gateway/dispatcher must reacquire its lease before sending. | Role ready, queue/outbox lag falls, exactly one lease owner per external lane, and no duplicate event. |
| Home node loss with Oracle reachable | Node condition false, home role gaps, external routes fail or converge | Check both sites separately; distinguish `chasebot` loss from `minecraftmachine` control-plane loss. Do not assume k3s can reschedule after its only server is gone. | Keep Oracle application capacity isolated until database writer freshness and home fencing are proven. Follow the controlled sequence in `PANTRYBOT-ACTIVE-ACTIVE-RUNBOOK.md`; do not merely scale Oracle up and point DNS at it. | New database epoch, stale home commit rejected, leases acquired only by Oracle, public routes return, synthetic event has one mutation/outbox/send. |
| Complete home outage or WAN loss | External checks fail while Oracle-local checks remain healthy; witness connectivity may be asymmetric | Test Cloudflare/Internet, witness, database, and local app independently. A home-side timeout is not proof that home is dead. | Automatic promotion is not authorized from a timeout alone. Obtain a neutral witness decision, fence the home writer and side-effect owners, promote Oracle, then route. If home cannot be fenced, keep Oracle read-only/recovery. | Record detection/promotion/RTO/RPO and route convergence. Gate remains open if old home writer could still commit or send. |
| Oracle outage | Oracle node/pod unavailable; home routes and leases still healthy | Verify home owner and home database; do not move ownership away from a healthy home owner because an idle standby is down | Leave home primary. Recreate only the Oracle capacity after Oracle control plane, secrets, replication, and witness relay are healthy. | Home continues one-owner operation; Oracle recovery issue recorded without changing home. |
| Split brain or lease loss | `witness_renewal_lost`, rejected fence, two apparent owners, duplicate/outbox retry spike | Stop external sends first; capture current epochs and logs from both sites. Never “fix” by deleting the newer lease row. | On lease loss, the affected gateway/dispatcher must disconnect/fail closed. Fence the old database writer before promotion. Restart a stale role only after it can acquire the current epoch. | Old token/epoch rejected, only one Twitch/overlay owner, no duplicate side effect, and post-incident queue reconciliation complete. |
| Database/storage corruption or replication lag | PostgreSQL readiness/recovery state, WAL/slot errors, queue/outbox failures, checksum/restore errors | Stop writers and event consumers; capture `pg_stat_replication`/receiver state and PVC identity without editing it. Do not run repair against the only copy. | Use the approved physical standby or an isolated backup restore from the PantryBot PostgreSQL authority runbooks. Promote only after source fencing and freshness/RPO are recorded. Re-seed the old site before making it a standby again. | Schema check, known synthetic row/queue/outbox reconciliation, current epoch, application readiness, and stale-writer rejection. |
| Secret or certificate failure | `CreateContainerConfigError`, auth/refresh errors, connector `/ready` failure, TLS/HTTP 401/403 | `kubectl get secret -o name`, pod events, and secret key names only; never use `-o yaml` or print data. Check certificate expiry through the provider/endpoint without exporting keys. | Reconstruct the named Secret from the approved escrow, apply the replacement, restart only affected pods, and roll back the Secret version if the new version fails. Do not copy private keys between sites in chat. | Pods consume the expected key names, OAuth/Twitch/Cloudflare behavior works, and no credential value entered logs/issues. |
| Cloudflare route or tunnel failure | Public curl fails, connector `/ready` fails, Cloudflare origin/connection errors, or Service has no endpoints | Check `kubectl -n pantry-bot get deploy,pods,svc,endpoints`; inspect connector logs and the documented route contract. Do not add the old `pantry-bot` origin back to the shared tunnel. | Restore the dedicated commands/app connector Secret or deployment from `62-`/`66-deployment-app-cloudflared.yaml`; change Cloudflare ingress only to the exact origins in `PANTRYBOT-PUBLIC-ROUTING.md`. Roll back the Cloudflare config version if origin checks regress. | Commands remains OAuth-free; OAuth/mod/overlay return their expected status; connector `/ready` is green at each intended site; no stale-origin errors. |
| Resource exhaustion | `kubectl top`, OOMKilled/evictions, queue/outbox age, node pressure, Cloud metrics | Inspect requests/limits and per-role logs; do not blindly add replicas if PostgreSQL or the free-tier budget is the bottleneck. | Reduce concurrency or pause nonessential workers; scale workers only when durable claims/idempotency and database capacity permit. Roll back a bad image/config rather than masking OOM with unlimited limits. | Queue drains, no OOM/eviction recurrence, database lag bounded, and free-tier capacity evidence attached to #191/#147. |
| Rollback or failback | New image, schema, promotion, or route fails verification | Preserve logs, epoch, backup/WAL position, and route version before reversing. Do not roll back a schema without its migration compatibility plan. | Stateless role: `kubectl -n pantry-bot rollout undo deployment/<role>` and verify. Database: keep the promoted writer, fence it, re-seed home, then perform the documented return-home sequence. Route: restore the prior known-good Cloudflare config only after origin checks. | Both sites agree on writer/epoch, old site is standby/recovery only, all route checks pass, and synthetic command has one outcome. |

## Controlled promotion sequence

Use only during an approved maintenance/failure exercise and record UTC times
in GitHub Issue #191 or PantryBot #147:

1. Confirm Oracle roles are Ready and identify the current home database writer.
2. Acquire a neutral witness authority decision; the witness is not itself a
   database fence.
3. Fence the home PostgreSQL writer and home gateway/dispatcher/overlay owners.
   Scaling pods down is not sufficient unless it also proves the old writer
   cannot commit or send.
4. Promote Oracle using the approved PostgreSQL authority procedure; record the
   new timeline/epoch and reject stale home tokens.
5. Start or unpause Oracle side-effect roles only after the database is writable
   and the application fence validates the new epoch.
6. Change Cloudflare origins/routes only after `/ready` and database checks pass.
7. Inject one synthetic event and verify one state mutation, one outbox record,
   and one delivered side effect.

The executable ownership rehearsals (`npm run rehearsal:ownership` and
`npm run rehearsal:failover`) are safe only against an isolated PostgreSQL
database. They do not promote production PostgreSQL, change DNS, or fence a
live writer.

## Known gates

- The witness grants authority but does not physically fence a remote database.
- PostgreSQL promotion, stale live-writer rejection, and provider-level route
  transition must be evidenced together before automatic failover is enabled.
- Cloudflare hostname configuration is external to these manifests.
- The legacy SQLite deployment is a recovery path, not a second HA writer.
- Do not put Minecraft or Cartwise into this failover sequence.
