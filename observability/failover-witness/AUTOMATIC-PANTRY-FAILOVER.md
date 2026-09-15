# PantryBot automatic failover: activation contract

This document describes required behavior and the workspace implementation as
of 2026-09-14. It is not evidence of a production deployment. The Canada laptop
is the last reported active site; Oracle automatic promotion must stay disabled
until the evidence below is collected. This work must not interrupt streaming.

## What the controller now implements

The Oracle contender polls the same `pantry:postgres` witness resource used by
Canada. A healthy holder makes acquisition return conflict. After acquiring an
epoch the controller immediately verifies renewal and starts a renewal thread
that runs every five seconds throughout slow promotion and rollout operations.
The sequence is:

1. Acquire and renew the shared lease.
2. Reject an unproven existing primary, then positively fence every other possible writer.
3. Check the target's replication identity, timeline and freshness.
4. Promote and observe `pg_is_in_recovery() = false`.
5. Change the local application database endpoint.
6. Start all seven application consumers and wait for deployment readiness.
7. Verify the application's authority, database and processing health.
8. Publish the site's public routes and verify the external result.
9. Continue renewing; loss invokes the local writer fence.

The controller writes a durable local activation receipt before promotion and
updates it after promotion and routing. It records the epoch and PostgreSQL
system identifier, never the lease token. After a process restart, an existing
primary can be adopted only if the witness grants the same epoch and its database
identity matches the receipt. A newer epoch, changed database, absent receipt,
or fenced receipt rejects adoption. The root-owned systemd state directory is
`/var/lib/pantry-postgres-promoter`; losing it safely prevents primary adoption.

Renewal failure sets a cancellation flag and immediately invokes the local
fence. The main controller checks that flag between phases and before each
mutating `kubectl` command. A slow or blocked subprocess cannot be assumed to
stop merely because its parent lost authority: the independently enforced
writer fence is still mandatory. Commands have bounded timeouts. An unverified
promotion, failed replication check, failed service check, route failure, or
lost authority aborts activation and fences the local site.

The witness persists a replacement state file and its directory before
acknowledging an epoch on Linux. A storage failure latches it unavailable so a
later request cannot receive an unpersisted token. It refuses startup with
missing state; `--initialize-state` is only for the first installation after
all writers are known fenced. A restored backup with older epochs is **not** a
safe automatic recovery procedure.

## Required adapter contracts

These are executable commands in the root-owned promoter environment file.
The controller refuses startup before lease acquisition if any are missing.
Commands must be root-owned, idempotent, and tested against the correct host.
A command returning zero is an assertion that its entire contract passed.

| Setting | Required behavior and positive evidence |
|---|---|
| `OLD_WRITER_FENCE_COMMAND` | Fence Canada **and** home as potential old writers; verify PostgreSQL cannot accept writes, existing database sessions cannot commit, and dispatchers cannot send. Check immutable host identity before acting. An SSH timeout, Tailscale offline status, expired lease, stopped Cloudflare connector, or newer epoch does not satisfy this contract. The old home-only GCP script is insufficient. |
| `LOCAL_WRITER_FENCE_COMMAND` | Revoke local writes and outgoing side effects, terminate existing connections, and prevent automatic container/Kubernetes restart from undoing the fence. Scope to PantryBot; do not blindly stop the site's entire k3s service. Local database and external routes must also be handled. Verify completion. |
| `REPLICATION_CHECK_COMMAND` | Verify database system identifier, timeline ancestry, last received/replayed WAL, uninterrupted replication evidence and schema. Wait for all received WAL to replay. Enforce an explicit approved RPO. A recent replay timestamp alone does not establish lag (an idle database can have an old timestamp). Reject a divergent already-primary Oracle database. When `PANTRY_RESUME_PRIMARY=true`, the controller has validated a durable same-epoch receipt; verify identity/schema and resume that activation, rather than treating the database as a fresh standby. |
| `SERVICE_CHECK_COMMAND` | Check the database endpoint points locally and is writable under current authority; check gateway, worker, dispatcher and overlay ownership plus public/private/API readiness. Check the worker contains the commands URL fix. Probe privately so standby tunnel health cannot disguise a missing authority lease. Do not send real Twitch messages as a health probe. |
| `PUBLISH_ROUTES_COMMAND` | Update commands/mods/overlay to the selected site's separate tunnel, exclude all standby origins, and verify public responses identify the selected site. Make partial updates resumable. If an update fails, report failure so the controller fences itself. Do not treat a healthy tunnel as authority. The local fence must withdraw or disable any partially published origins. |

There are deliberately no placeholder commands returning success. The adapters
are the actual safety mechanisms; replacing them with `true` creates an unsafe
controller. Both fence commands need independent verification rather than
trusting a successful shell exit from an unvalidated script.

Hook environment includes `PANTRY_PROMOTION_SITE`, `PANTRY_PROMOTION_RESOURCE`,
`PANTRY_PROMOTION_EPOCH`, `PANTRY_DATABASE_SYSTEM_IDENTIFIER`, and
`PANTRY_RESUME_PRIMARY`. A local fence can run before identity discovery, so it
must remain effective when those identity fields are empty. Hooks must not
print credentials inherited from the promoter's environment.

## Evidence required before enabling production promotion

| Area | Evidence that passes the gate | Safe work during streaming |
|---|---|---|
| Current authority | Canada alone renews `pantry:postgres`; no duplicate supervisor steals/reuses the token; all applications remain healthy across many lease periods | Read-only lease and supervisor observations, with tokens redacted |
| Database standby | Oracle streams from the **current Canada primary**, matching system identifier and timeline; replay/receive LSN and replication slots measured; no divergent writer | Read-only checks; prepare a separate standby if rebuilding old standby would affect other services |
| Fence correctness | Exact Canada/home/Oracle identities, scope and return conditions verified; restarting an old host cannot automatically become writable | Inspect scripts, task policies and container restart settings; test with disposable runtimes |
| Partition handling | Old holder loses witness but retains Twitch and database connectivity; DB writes and sends stop before a successor is exposed | Isolated witness resource, fake endpoints and disposable DB only |
| Long activation | Artificially slow promotion/rollout exceeds 30 seconds while the contender continuously renews; forced renewal loss blocks all later phases | Unit/integration rehearsal in isolated fixtures |
| Restart recovery | Controller crash between promotion and route change resumes only with durable same-epoch receipt; stale/unknown primary stays fenced | Receipt logic and unit cases are implemented; verify the actual database and route adapters in an isolated process-kill rehearsal |
| Durable witness | State survives restart with monotonic epochs; disk errors deny acquisitions and renewals; missing/corrupt/old state never initializes silently | Disposable state files; production restarts require a planned change because GCP is the authority dependency |
| Routing | Isolated test hostnames move only after target health and authority; partial route failure does not expose a stale writer | Separate Cloudflare hostnames and test tunnels; do not move production DNS during stream |
| Recovery/rejoin | Old primary remains fenced after returning; a reviewed re-seed/rewind establishes the current timeline before standby service restarts | Script inspection and disposable PostgreSQL integration test |
| Functional behavior | Readiness, ingress, worker processing, dispatcher, commands URL and overlay reconnect work against the selected site; ambiguous Twitch send/crash behavior has explicit retry semantics | Unit tests and disposable Twitch channel with explicit send authorization |

## Availability limits that remain

The current lease is not a database lock. The Canada PowerShell supervisor
stopping application containers does not stop direct PostgreSQL writers, nor
does it guarantee termination during a paused VM or blocked Docker call. A
software polling interval alone cannot establish hard fencing under arbitrary
host suspension. An out-of-band power fence or a demonstrated enforcement
mechanism covering PostgreSQL and external sends is needed before unattended
partition failover can be considered safe.

The GCP witness is an availability dependency. All sites must reach it through
paths that do not depend on the failed active site. Canada's current Oracle
relay makes Oracle a dependency for Canada's authority. A GCP outage must
fail closed until durable state and connectivity recover. More replicas in one
host do not change this site failure domain.

The contender detects lease loss, not every application failure. If the active
site's worker dies but its supervisor keeps renewing the database lease,
automatic database promotion is not an appropriate first response. Local
supervision should restart failed roles; a health escalation policy needs a
verified fence and intentional authority handoff before site promotion.

Do not claim complete automatic failover until the adapters, restart recovery,
standby replication, and isolated end-to-end rehearsal pass, then complete a
planned live cutover when a brief recovery interval is acceptable. The unit
tests validate ordering and lease behavior; they do not validate production
fence effectiveness or establish zero downtime.

## Local validation

From this directory, run:

```text
python test_oracle_promoter.py
python test_witness.py
```

The promoter runner now executes every `test_*` function, including prior
promotion-verification tests that its old direct runner silently skipped.
Production resource names, routes and lease tokens are not used by these tests.
