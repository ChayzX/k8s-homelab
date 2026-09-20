# Authentik PostgreSQL promotion and failback runbook

**Status:** controlled procedure; not an authorization to promote

**Scope:** the Authentik PostgreSQL database in namespace `auth`, home
(`MinecraftMachine`) and the independent Oracle k3s site. This runbook is
separate from PantryBot PostgreSQL and from the Authentik application
capacity overlays. It must be executed only during an approved maintenance
window by an operator who can reach both clusters and the neutral witness.

The normal state is one writer: the home StatefulSet
`auth-postgresql-home-primary` is writable and the Oracle StatefulSet
`auth-postgresql-standby` is a physical standby. Authentik server, worker, and
LDAP capacity may exist at both sites, but Oracle must not receive interactive
write traffic while its database is in recovery. A pod being Ready, a Service
having endpoints, or a route being reachable is not database authority proof.

The procedure has four safety invariants:

1. A retained, independently verified backup exists before any role change.
2. The old writer is fenced and its stale-write rejection is independently
   observable before the new writer is promoted or routed.
3. A neutral witness returns a strictly newer `auth:postgres` epoch for the
   new writer; no site infers authority from a Kubernetes label or route.
4. Failback is a separate transition. Automatic failback is disabled.

Do not record passwords, tokens, kubeconfig contents, private-key material,
or Secret values in command output, commits, or GitHub Issues.

## Current topology and controls

| Role | Home | Oracle |
| --- | --- | --- |
| Normal PostgreSQL role | `auth-postgresql-home-primary-0`, writable | `auth-postgresql-standby-0`, recovery/standby |
| Normal Service | `auth-postgresql-home-primary` | `auth-postgresql-standby` |
| Promotion target | no | `auth-postgresql-standby-0` |
| Local fence adapter | `fence-authentik-postgres-home.sh` | `fence-authentik-postgres-oracle.sh` |
| Remote old-writer transport | Oracle-side `fence-authentik-home-from-oracle.sh` | n/a |
| Controller | home self-fence example only | `authentik_oracle_promoter.py` (guarded/disabled) |

The checked-in controller requires both a local Authentik fence command and an
explicit old-writer fence command. It rejects missing, non-executable,
malformed, or PantryBot-only fence commands before acquiring a witness lease.
The example systemd unit is documentation only until the host transport,
known-hosts entry, key permissions, sudo rule, and dry-run evidence have been
installed and reviewed on both sites.

Authoritative references:

- [`AUTHENTIK-HA-READINESS.md`](AUTHENTIK-HA-READINESS.md) — current readiness
  and open gates.
- [`RESTORE-REHEARSAL.md`](RESTORE-REHEARSAL.md) — retained backup and
  isolated-restore evidence.
- [`SERVICE-FAILOVER-GATES.md`](SERVICE-FAILOVER-GATES.md) — common state,
  side-effect, and failure gates.
- `observability/failover-witness/authentik_oracle_promoter.py` — guarded
  controller contract.
- `observability/failover-witness/fence-authentik-*.sh` — fixed-identity
  fence adapters.
- GitHub Issue `ChayzX/k8s-homelab#202` — durable execution evidence.

## Evidence record

Create a sanitized evidence record before starting. Use UTC and monotonic
timestamps where possible. A record is incomplete until every field below has
an observed value or an explicit `not measured` reason.

```text
run_id=<operator-generated non-secret id>
window_start_utc=<timestamp>
home_context=<context name only>
oracle_context=<context name only>
source_writer=<home|oracle>
source_epoch=<witness epoch>
source_system_identifier=<sanitized PostgreSQL system id, if approved>
source_lsn=<flush/replay LSN pair>
backup_object=<R2 object name only>
backup_generation=<generation>
backup_bytes=<integer>
backup_sha256=<digest>
backup_verified_utc=<timestamp>
last_replay_utc=<timestamp>
declared_rpo_seconds=<integer>
fence_requested_utc=<timestamp>
fence_confirmed_utc=<timestamp>
fence_observation=<sanitized endpoint/pod/transaction result>
promotion_requested_utc=<timestamp>
promotion_confirmed_utc=<timestamp>
new_epoch=<strictly newer witness epoch>
route_switch_requested_utc=<timestamp>
route_converged_utc=<timestamp>
measured_rto_seconds=<integer>
measured_rpo_seconds=<integer>
session_validation=<login/callback/LDAP-bind/logout/pre-existing-session results>
stale_writer_result=<rejected|not-tested|failed>
failback_run_id=<id or not-applicable>
rollback_result=<description>
operator=<GitHub identity>
```

Attach only sanitized output and links to the issue. Keep raw logs and secret
material in the approved restricted evidence store.

For credentialed application checks, the evidence record must also include:

```text
credential_source=operator-supplied disposable only
credential_values_not_recorded=true
credential_scope=<disposable identity/provider or isolated restore>
```

Do not use a retained production password, API token, recovery code, or
session cookie as a test credential. If an operator cannot supply a disposable
identity through the approved restricted channel, set `session_validation=not
measured` and keep the issue open.

Timing fields are valid only when their source markers were captured during the
same rehearsal. At minimum, retain sanitized values for:

```text
failure_injection_utc=<timestamp>
source_commit_or_flush_lsn_utc=<timestamp and LSN, or not measured>
fence_confirmed_utc=<timestamp>
target_replay_or_visible_commit_utc=<timestamp and LSN, or not measured>
route_converged_utc=<timestamp>
```

If `failure_injection_utc` or either source/target commit marker is missing,
set `measured_rto_seconds=not measured` and/or
`measured_rpo_seconds=not measured`; do not infer a formal RTO/RPO from pod
readiness, route health, or broad timestamp bounds.

## Phase 0 — stop conditions and preflight

Stop immediately, leave home as the sole writer, and comment the reason on
Issue #202 if any check fails.

1. Confirm the maintenance window, named operators, and a tested management
   path to home, Oracle, and the witness. Do not depend on Authentik for the
   emergency SSH path.
2. Select contexts explicitly and prove their identity with the repository's
   target assertion command. Never use the current kubectl context implicitly.
3. Confirm no other Authentik promotion, restore, or failback job is running.
4. Confirm the witness `/healthz` endpoint is reachable and the current
   `auth:postgres` owner/epoch is readable without exposing its secret.
5. Confirm home is the only writable database:

   ```bash
   kubectl --context "$HOME_CONTEXT" -n auth exec auth-postgresql-home-primary-0 -- \
     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
     'select pg_is_in_recovery(), pg_current_wal_lsn();'
   kubectl --context "$ORACLE_CONTEXT" -n auth exec auth-postgresql-standby-0 -- \
     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
     'select pg_is_in_recovery(), pg_last_wal_replay_lsn();'
   ```

   Inject credentials through the existing Secret-backed container environment;
   never place values in the shell history or evidence. Expected normal output
   is home `f` and Oracle `t`.
6. Check replication freshness from `pg_stat_replication` and standby replay
   position. Record the observed lag and compare it with the declared RPO. If
   Oracle is not streaming or its lag exceeds the declared RPO, stop.
7. Verify Authentik server, worker, and LDAP outpost are healthy at the target
   site, but keep write-capable application roles disabled until after
   promotion and endpoint switching.
8. Verify the target Service selector and target PostgreSQL container are the
   fixed identities expected by the controller. A selector drift is a stop
   condition.

## Phase 1 — backup and rollback point

Before fencing or promoting:

1. Run the approved `scripts/authentik-postgres-backup.sh` path against the
   current writer. It must complete with a local dump, R2 transfer, Rclone
   check, and exact remote byte-count match.
2. Record object name, generation, byte count, SHA-256, and completion time.
   Verify that the dump can be read by `pg_restore` in an isolated target or
   rely on a recent retained isolated-restore record whose artifact identity
   matches this generation.
3. Preserve the current home PVC and Secret references. Do not delete or
   overwrite a PVC to make space for the rehearsal.
4. Capture a final source WAL/LSN and witness epoch immediately before the
   fence. This is the upper bound for the measured data-loss window.

If the backup or restore-path check fails, do not continue. The backup is a
recovery point, not a substitute for fencing.

## Phase 2 — validate fencing transport (no promotion)

Perform this phase before the outage rehearsal, and repeat the dry-run in the
same window if host state changed.

1. From Oracle, run the fixed-identity transport in dry-run mode:

   ```bash
   /usr/local/lib/failover-witness/fence-authentik-home-from-oracle.sh --dry-run
   ```

   It must verify the pinned home address, user, SSH key, known-hosts file,
   remote sudo policy, and the home adapter's fixed Service selector. A
   successful SSH connection alone is not fence proof.
2. On home, run the local adapter in dry-run mode:

   ```bash
   /usr/local/lib/failover-witness/fence-authentik-postgres-home.sh --dry-run
   ```

3. Verify the installed files match reviewed repository content, are root-owned
   and non-writable by the database/application users, and have no shell
   interpolation of operator-provided commands.
4. Confirm the home adapter is capable of proving the PostgreSQL Service has
   no endpoints after the StatefulSet is stopped. Do not invoke `--confirm`
   during preflight.

If transport, identity, permissions, or known-hosts checks fail, stop. Do not
replace them with a route change, tunnel shutdown, pod deletion, or scaling an
application Deployment.

## Phase 3 — controlled Oracle promotion

This phase is only allowed after Issue #202 has recorded successful Phase 0–2
evidence and the operator has explicitly approved the outage test.

1. Record the failure-injection timestamp and make the home application
   write paths unavailable or read-only. Keep the home database process
   available long enough for the dedicated fence to verify its result.
2. Acquire the neutral witness transition for `site=oracle,
   resource=auth:postgres`. The response must contain a token and an epoch
   greater than the recorded home epoch. If the witness refuses, stop.
3. Execute the Oracle-to-home old-writer fence with `--confirm`. Require the
   exact success marker `fence_status=passed
   scope=home-authentik-postgres` and retain only that sanitized result.
4. Independently verify home stale-write rejection. Use a harmless,
   pre-approved transaction probe or endpoint status check that can prove the
   old Service has no endpoint and cannot commit. Do not use a production data
   mutation as a probe. A missing endpoint alone is insufficient if a direct
   old-writer path remains reachable.
5. Promote Oracle only after fencing and stale-write rejection pass. The
   guarded controller performs `pg_ctl promote`, waits for
   `pg_is_in_recovery() = false`, and labels the target pod. If performing
   this manually for a reviewed rehearsal, use the same bounded command and
   checks; do not edit labels as a substitute for promotion.
6. Verify Oracle's database identity, schema, and replay position. Record the
   promotion timestamp and calculate the observed RPO from the final home LSN
   and Oracle replay LSN.
7. Switch the Authentik PostgreSQL Service selector to the promoted Oracle
   primary and update the Authentik database host/port Secret through the
   approved Secret mechanism. Restart server and worker deployments only after
   the database is writable; wait for rollout completion. Enable LDAP outpost
   and other write-capable roles only after application checks pass.
8. Keep the home database fenced and its Service unroutable. Do not re-enable
   home by merely scaling its StatefulSet or restoring a route.

## Phase 4 — application, session, and routing validation

Run all checks against the actual promoted endpoint, with disposable test
identities where a credential is needed. Record status and timestamps, never
credential values.

- PostgreSQL: `pg_is_in_recovery() = false`; a transaction committed by the
  approved synthetic probe is visible on the promoted writer.
- Authentik readiness: server and worker are Ready; migrations are complete;
  LDAP outpost is Ready.
- Login: a disposable identity completes login and callback; provider and
  certificate behavior match the pre-failure contract.
- LDAP: a disposable bind succeeds through the intended outpost.
- Session: a pre-existing session either survives or is deliberately rejected
  according to the documented policy; record which occurred.
- Logout: logout invalidates the test session.
- Routing: external and internal routes converge on Oracle; direct home
  writer paths remain fenced.
- Monitoring: external observation sees the expected endpoint and no duplicate
  writer alert remains unresolved.

Measure:

```text
RTO = route_converged_utc - failure_injection_utc
RPO = source_last_commit_or_flush_utc - target_replay_or_visible_commit_utc
```

If session/provider/LDAP/routing validation fails, stop serving writes, keep
Oracle isolated, and use the rollback procedure below. Do not call readiness
alone a successful promotion.

## Phase 5 — rollback during a failed promotion

Rollback is not failback and must not create a second writer.

1. Stop Authentik server/worker/LDAP write-capable roles at Oracle and remove
   public write routing.
2. Preserve Oracle logs, database status, current epoch, and the exact backup
   identity. Do not discard the promoted PVC.
3. If any Oracle transaction was accepted, take a final Oracle backup and
   record its WAL/LSN before fencing Oracle.
4. Confirm the home writer remains fenced. If it was accidentally re-enabled,
   fence it again and verify stale-write rejection before proceeding.
5. Follow the return-home sequence below only after Oracle is fenced and a
   reviewed restore/reseed target is ready. Never point the Service at a
   standby in recovery.

## Phase 6 — controlled return-home failback

Failback requires a separate maintenance approval and a new evidence record.
Keep Oracle as the sole writer until every prerequisite passes.

### Preconditions

- Oracle is the sole writable Authentik PostgreSQL authority and has a final
  verified backup.
- Oracle Authentik application roles are stopped or read-only.
- Home's old writer and all direct home endpoints are fenced; the preserved
  home PVC is not assumed current.
- A fresh home recovery target/PVC is available. Do not overwrite the old PVC.
- The reverse replication or logical restore transport has been tested and the
  target reports `pg_is_in_recovery() = true`.
- Home has enough disk and a unique replication slot/Secret reference.
- The witness can return an epoch strictly newer than Oracle's promotion epoch.

### Ordered failback

1. Capture Oracle final backup, SHA-256, generation, WAL/LSN, and current
   witness epoch.
2. Stop Oracle Authentik server, worker, and LDAP write-capable roles. Verify
   no application transaction is in flight.
3. Seed a new home standby from Oracle. Verify schema, role, replication
   credentials, `pg_is_in_recovery() = true`, and replay catch-up to the
   selected RPO. Record source flush LSN, target replay LSN, and lag.
4. Fence Oracle using the fixed-identity Oracle fence adapter and verify its
   Service has no endpoints and stale writes are rejected. Do not use the home
   fence adapter here; it may target the home-return standby.
5. Acquire `auth:postgres` for home. Reject the transition unless the returned
   epoch is strictly newer than the Oracle epoch and the old-writer fence is
   recorded as passed.
6. Promote the home target and verify `pg_is_in_recovery() = false` plus the
   approved synthetic transaction probe.
7. Switch the Authentik Service/Secret endpoint to home, restart server and
   worker, and verify all Phase 4 application/session/routing checks.
8. Recreate Oracle as a standby from home. Leave Oracle application roles
   stopped until the standby and fencing posture are verified.
9. Record final writer, epoch, replication direction, RTO/RPO, and rollback
   result on Issue #202. Keep both old PVCs until the evidence is reviewed.

## Abort and recovery rules

- Any missing backup, ambiguous target, stale epoch, failed fence, unknown
  endpoint, or failed session check is an abort.
- Abort means stop new writes, preserve both database copies, and retain the
  last known sole writer. Never “try the other site” by changing a Service
  selector while the target is in recovery.
- Do not delete PVCs, drop databases, remove credentials, or clean old
  infrastructure during this runbook. Those actions require a separate issue,
  verified backup, and documented restore path.
- If the witness is unreachable, no automatic decision is safe. Keep the
  current writer and use the independent emergency access path.

## Completion criteria for Issue #202

Issue #202 may be closed only when sanitized evidence proves all of the
following in the same or separately linked rehearsals:

- retained backup and isolated restore path;
- old-writer fence transport installed, dry-run verified, and confirm result;
- stale writes rejected after the old writer is fenced;
- strictly newer witness epoch and one promoted PostgreSQL writer;
- Authentik login, callback, provider/certificate, LDAP bind, logout, and
  pre-existing-session behavior;
- external/internal routing convergence;
- measured RTO and RPO within the declared target;
- controlled rollback or return-home failback;
- final replication direction and sole writer verified;
- no production Secret values in the evidence.

Until those records exist, retain the issue as open and keep home-primary /
Oracle-standby posture. The repository tests validate policy contracts only;
they do not close the live promotion gate.
