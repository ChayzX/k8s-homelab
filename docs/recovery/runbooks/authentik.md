# Authentik, LDAP, and PostgreSQL disaster recovery SOP

## Current topology and authority

The base Authentik server/worker/PostgreSQL installation is a Helm deployment
outside this repository. The reproducible additions are `auth/50-ldap-outpost.yaml`
and `auth/60-postgresql-transport.yaml`. Current normal authority is:

- home `auth-authentik-server` and `auth-authentik-worker`;
- home `auth-postgresql-0` on a local-path PVC;
- home `ldap-outpost` (one replica, Service ports 389/636); and
- Oracle `auth-postgresql-standby-0` as a physical, read-only standby.

Oracle Authentik server/worker/LDAP remain stopped until promotion, secret,
provider/session, and rollback gates pass. `auth-postgresql-transport` is a
replication transport, not an application endpoint. Use
`docs/recovery/AUTHENTIK-HA-READINESS.md` and `docs/recovery/RESTORE-REHEARSAL.md`
as the authority for promotion decisions.

## Normal health check

```bash
kubectl -n auth get deploy,sts,pods,svc,pvc -o wide
kubectl -n auth get endpoints ldap-outpost auth-postgresql-transport
kubectl -n auth describe pod auth-postgresql-0
kubectl -n auth logs deploy/auth-authentik-server --since=30m --tail=150
curl --max-time 15 -fsS https://auth.greeniespantry.uk/-/health/ready/
```

For replication, inspect sanitized status only from the database pods. The
expected states are home `pg_stat_replication=streaming` and Oracle
`pg_stat_wal_receiver=streaming`, with comparable LSNs. Do not print passwords,
connection strings containing passwords, or Secret data.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Deployment availability, pod restarts, `/-/health/ready/`, LDAP bind failures | Events, previous logs, image/config references, and `kubectl get secret -o name`; keep the DB untouched | Restart or roll back the affected Deployment. For LDAP, apply the tracked outpost manifest only after its token key exists. Do not start Oracle Authentik against a standby. | Authentik ready, a synthetic login/provider check succeeds where applicable, LDAP/LDAPS bind works, and direct emergency SSH still works. |
| Home node loss | Node `Ready` false; home Authentik and DB disappear | Determine whether the DB PVC is still attached and whether the control plane can act; do not force-detach a local-path volume | Keep Oracle standby read-only until home DB writer is fenced. A controlled promotion must stop/fence home PostgreSQL and start Oracle DB as the sole writer before starting Oracle apps. | New writer writable, old home writer rejects writes, Authentik session/provider test passes, routing converges, and rollback path is recorded. |
| Home outage or WAN loss | Auth URL and LDAP external checks fail; Oracle receiver may stop receiving WAL | Separate home failure from Cloudflare/LDAP network failure; verify independent Oracle management and last replay LSN | Do not promote on network timeout alone. Use a neutral decision and an authoritative fence. If home cannot be fenced, use the isolated restore procedure, not a second live writer. | Emergency SSH/monitoring path works without Authentik; RPO is measured; one writer and one route are documented. |
| Oracle outage | Standby receiver/Oracle node unavailable while home remains healthy | Verify home `pg_stat_replication` and home application; do not stop home services | Keep home primary. Repair/reseed Oracle from the current home backup/WAL path; do not route users to a stopped standby. | Home login, LDAP, and monitoring stay healthy; Oracle catches up before being considered standby again. |
| Split brain or stale writer | Two writable PostgreSQL instances, timeline mismatch, unexpected commits, provider/session inconsistency | Stop Authentik writers and record `pg_is_in_recovery`, timelines, LSNs, and pod/PVC identities. Never delete a PVC during diagnosis. | Fence the stale host at the database and host/site level. Keep the newest authoritative writer; re-seed the other PVC from it. Do not rely on k3s scale-down as a fence. | Old writer commit test fails; exactly one writable DB; Authentik migrations/provider state consistent; stale sessions are handled by the documented rollback decision. |
| PostgreSQL database-storage/PVC corruption or bad restore | PostgreSQL crash recovery, checksum/WAL errors, PVC mount failure, `pg_restore` errors | Stop applications and preserve the PVC/snapshot. Select a known dump/WAL generation and checksum from the recovery inventory. | Restore into an isolated PostgreSQL target first using the procedure in `RESTORE-REHEARSAL.md`. Production replacement requires a reviewed backup/failover window and a fresh Secret/provider reconstruction. | `pg_restore --list`, schema/data sanity, Authentik readiness, synthetic login, LDAP provider test, and no production route enabled during rehearsal. |
| Secret, signing key, provider, or certificate failure | Authentik 500/401, outpost reconnect loop, LDAPS TLS error, SSSD bind failure | Check Secret names, key names, pod events, certificate expiry and provider status by name only. Never dump Secret YAML. | Reconstruct from approved escrow; restart only consumers. If signing/provider secrets changed, treat existing sessions as potentially invalid and follow the planned session impact/rollback. | Synthetic user login, provider callback, LDAP/LDAPS bind, SSSD `getent`/`id`, and emergency direct SSH all pass without exposing values. |
| Auth/routing/tunnel failure | `auth.greeniespantry.uk` fails while pod ready, Cloudflare connector not ready, or private app gets 502 | Compare in-cluster service/endpoint/readiness with external curl; inspect shared tunnel origin and certificate mode without altering routes first | Restore the exact documented Authentik origin and connector Secret/config. Roll back the Cloudflare config version if the previous known-good route is safer. Do not point the route at a standby DB-backed app. | External readiness, login callback, Grafana/Operations protected route, LDAP/LDAPS checks, and no stale origin errors. |
| Resource exhaustion | OOMKilled, PostgreSQL connection exhaustion, WAL growth, node disk pressure | `kubectl top`, pod events, PVC usage, replication slot/WAL retention; do not increase limits beyond measured free-tier capacity | Pause nonessential workers, clean only documented temporary data, and restore a known-good resource/image version. Never delete PostgreSQL WAL/PVC contents manually. | Stable DB, receiver caught up, Authentik readiness, and backup job completes within the capacity budget. |
| Rollback/failback | New DB/provider/image/route fails the post-promotion checks | Preserve old/new epochs, timelines, checksums, route version, and session behavior before reversing | Keep one writer. Fence the promoted writer, re-seed home if needed, promote home through the same controlled sequence, then start home apps and route traffic. Never start both DB writers to “compare.” | Home is writable, Oracle is streaming standby, stale Oracle writer is rejected, login/provider/LDAP checks pass, and the incident is attached to #198/#202. |

## Backup and isolated restore

The daily backup is `scripts/authentik-postgres-backup.sh`; its destination and
retention are documented in `RESTORE-REHEARSAL.md`. Use the selected artifact's
checksum, restore it into a temporary namespace/instance, and delete that
target after evidence capture. The existing isolated restore proves database
restore and Authentik readiness, but a complete interactive session/provider
reconstruction is a separate gate.

## Controlled promotion checklist

1. Confirm the latest standby replay position and backup generation.
2. Confirm direct SSH, monitoring, and an Authentik-independent recovery path.
3. Stop/fence home Authentik server/worker and PostgreSQL; verify no home writer
   can commit. This is the crucial safety boundary.
4. Promote Oracle PostgreSQL and record timeline/LSN; verify read/write state.
5. Reconstruct and validate Authentik secrets/providers/outpost before starting
   Oracle server, worker, or LDAP.
6. Start Oracle apps, validate readiness/login/LDAP, then change routing.
7. Record RTO/RPO and retain home artifacts for reverse promotion.

## Known gates

- Full session/provider reconstruction and a production-like login are not
  implied by PostgreSQL streaming.
- Physical STONITH or an equivalent complete host/database fence is required
  before automatic promotion. The witness alone is not a database fence.
- Authentik must not become a prerequisite for the recovery path itself.
