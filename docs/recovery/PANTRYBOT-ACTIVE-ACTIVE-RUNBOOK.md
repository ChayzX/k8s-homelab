# PantryBot active-active runbook

This runbook is the operational contract for the split PantryBot roles.
Minecraft is not part of this runbook.

## Current live state (2026-09-13)

The production split-role deployment is live at both sites. The public
commands-only Cloudflare route is served by a dedicated connector in home and
Oracle; the private API/site, workers, overlay delivery, gateway, and
dispatcher are also Ready at both sites. The native PantryBot suite currently
passes 121 test files and 617 tests.

The current controlled failover state is Oracle-primary: Oracle's PantryBot and
Authentik PostgreSQL instances are writable and Oracle owns all external-side-
effect leases. The production home PantryBot and Authentik database
StatefulSets remain fenced at zero replicas with zero production Service
endpoints. A separate home-return PostgreSQL StatefulSet is running as a
read-only physical standby (`pg_is_in_recovery()=t`, WAL receiver
`streaming`) and is not an application authority. Home Authentik HTTP capacity
remains available against the Oracle writer so the home CI tunnel can serve the
identity hostname. This is a deliberate single-writer recovery state, not
uncontrolled multi-primary database operation.

The dedicated commands hostname is intentionally OAuth-free: its public site
and `/api/public/commands` are available, while `/login/*`, `/oauth/*`,
`/mod/*`, and private `/api/*` paths return 404. OAuth, moderator, and overlay
routes remain on their separate hostnames.

## Controlled failover evidence — 2026-09-13

The live rehearsal acquired witness epoch 19, fenced home PantryBot PostgreSQL
through GCP -> ChaseBot using the least-privilege `pantry-postgres-fencer`
ServiceAccount, promoted Oracle, switched the Oracle Service to the primary
pod, and verified PantryBot readiness. Home Authentik capacity was then stopped,
home Authentik PostgreSQL was fenced through the corresponding restricted
fencer, Oracle Authentik PostgreSQL was promoted, and home Authentik HTTP
capacity was restored against the Oracle NodePort writer. External checks
returned 200 for commands, the public commands API, OAuth, and Authentik routes.
Oracle holds the gateway, dispatcher, and overlay leases.

The rehearsal exposed and corrected two adapter defects: multi-argument SSH
fence commands were initially treated as one executable, and the ChaseBot
forced-command wrapper did not accept GCP's explicit `--confirm` suffix. The
replacement adapter is committed in homelab commits `560151d` and `dd8dd04`.
Return-home and synthetic-event proof remain separate gates. The Oracle-side
automatic detector is now enabled by a 30-second systemd timer and has been
verified to exit cleanly with `oracle_already_primary` while Oracle is already
the authority. A live home-loss promotion and its RTO/RPO measurement remain
unproven.

## Target topology

Home and Oracle each run independent k3s application capacity:

- public commands UI and private UI are stateless HTTP capacity;
- private API replicas serve OAuth/moderator routes and use the encrypted
  PostgreSQL credential authority;
- Twitch gateway replicas exist in both sites, but only the lease holder owns
  the Twitch connection;
- workers consume the shared durable event queue and may run concurrently;
- PostgreSQL-backed moderator mutations enter the same durable queue; workers
  apply them with transactional audit/result records and enqueue Twitch
  announcements through the outbox;
- Twitch chat, Twitch moderation, and overlay dispatchers each have their own
  target lease and fencing epoch;
- PostgreSQL has one writable primary per database epoch. Application
  active-active does not mean uncontrolled multi-primary database writes.

The home and Oracle k3s control planes remain independent. Do not stretch k3s
embedded etcd over the WAN.

## Database authority

The current free-tier database authority candidate is documented in
`PANTRYBOT-POSTGRES-AUTHORITY-CANDIDATE.md`. Its disposable physical-streaming
manifests and executable gate are the only approved next rehearsal. The older
logical-replication manifests remain transport experiments and must not be
treated as promotion or fencing evidence.

The production authority must provide:

1. durable PostgreSQL state for domain tables, event queue, outbox, leases, and
   encrypted Twitch credentials;
2. a primary/standby or equivalent promotion mechanism;
3. a fencing authority that can reject the old primary before routing writes to
   the promoted primary;
4. encrypted backups and WAL retention within the free-cost budget; and
5. a tested client endpoint that follows the current primary without changing
   application credentials.

The current home `local-path` PVCs and the Oracle application host are not by
themselves a database HA solution. Authentik's PostgreSQL is a separate
workstream and must not be reused for PantryBot rehearsal or production state.

The legacy SQLite deployment remains scaled to zero and recoverable for
rollback reference only. Production roles use the PostgreSQL authority
described above; the database endpoint must not be changed outside the
controlled promotion/failback procedure.

## Normal operation

1. Start the database authority and verify the current primary epoch.
2. Run API/UI, gateway, worker, and dispatcher capacity in both sites with
   site-specific `PANTRY_SITE_ID` and `PANTRY_INSTANCE_ID`.
3. Verify exactly one owner for `pantry:twitch:ingress`.
4. Verify one owner each for `twitch.chat`, `twitch.moderation`, and `overlay`
   dispatcher resources.
5. Confirm queue and outbox lag, dead letters, lease renewal errors, and public
   readiness through external monitoring.
6. Keep the public commands and moderator hostnames routed only to ready
   origins. The public commands path must never redirect to OAuth.

## Controlled home-to-Oracle promotion

Use an isolated environment first. Every command must record UTC timestamps in
GitHub Issue #191.

1. Confirm Oracle application pods are ready and can reach the database
   endpoint, Twitch APIs, and overlay destination.
2. Confirm the home database writer and all home external-side-effect leases.
3. Fence both home database writers using the independently reachable,
   least-privilege PantryBot and Authentik fence adapters. Do not merely scale
   the home pods down and assume fencing.
4. Promote both Oracle database instances and record the new database epoch for
   each. The PantryBot promotion adapter requires `HOME_FENCE_COMMAND` and
   `AUTH_HOME_FENCE_COMMAND`; it refuses to proceed if either adapter is absent.
5. Verify stale home database credentials cannot commit a write.
6. Allow the Oracle gateway and target dispatchers to acquire their leases.
7. Verify the old home gateway is disconnected and the old dispatchers reject
   `assertFence` before any send.
8. Route public/API traffic to Oracle only after `/readyz` and database checks
   pass.
9. Inject a synthetic chat event and verify one state mutation, one durable
   outbox row, and one delivered side effect.
10. Record detection time, promotion time, route convergence, RTO, RPO, and
    duplicate behavior.

The application lease rehearsal has now been run against a real disposable
PostgreSQL instance on `chasebot` and passed: home acquired epochs 1 and 3,
Oracle was rejected while home owned the lease, and stale home ownership was
rejected after takeover at epochs 2 and 4. The disposable namespace was
deleted afterward. This proves the PostgreSQL schema/lease path, but it does
not yet prove cross-site database promotion or live Twitch routing.

The live platform rehearsal was also run against the current Oracle writer on
2026-09-13. It inserted a unique synthetic event, rejected its duplicate,
claimed and completed it, inserted a unique rehearsal outbox row, rejected its
duplicate, and claimed and completed the outbox row. Both final statuses were
`completed`. The rehearsal target is non-external and sends nothing to Twitch
or the overlay; live EventSub-to-worker-to-dispatcher behavior remains a
separate provider-side gate.

The same durable event/outbox rehearsal also passed against a disposable
PostgreSQL 16 instance on the Oracle arm64 k3s node through an SSH tunnel. The
Oracle namespace was deleted after the check. This proves application
portability on both architectures, not replication or automatic promotion.

A disposable cross-site logical-replication rehearsal also passed. Oracle's
PostgreSQL pod reached the home/chasebot PostgreSQL NodePort, created a
subscription, and observed a row written on home. The subscription and both
temporary namespaces were removed afterward. This is asynchronous, one-way
replication: it demonstrates transport and WAL configuration, but does not
provide multi-primary writes, automatic promotion, or a measured zero-RPO
guarantee. The allowed RPO for this design therefore remains the replication
lag at the failure boundary until a promotion controller and fencing test are
proven.

The physical PostgreSQL transport was first exercised against the live home
authority with a disposable Oracle standby. That historical rehearsal proved
transport and promotion mechanics, but did not fence the home writer. The
current production state has since been deliberately left in an
Oracle-primary/home-fenced recovery window. The promotion adapter now models
the coupled PantryBot/Authentik fence-and-promote order; the remaining gates
are a controlled return-home rehearsal proving the reverse source fence and
application fence against the live authority, plus measured automatic
promotion and live synthetic-event evidence.

The application-side promotion check is reproducible from the PantryBot
worktree with `npm run rehearsal:promotion`. Set
`PANTRY_HOME_DATABASE_URL`, `PANTRY_ORACLE_DATABASE_URL`,
`PANTRY_REHEARSAL_RESOURCE`, `PANTRY_REHEARSAL_HOME_OWNER`, and
`PANTRY_REHEARSAL_ORACLE_OWNER` to disposable databases only. The command
expects the Oracle database to have already been made writable by the
operator/controller; it waits for the replicated lease row, advances the
epoch after home expiry, and verifies the stale home token is rejected on
Oracle. It does not promote PostgreSQL or fence the old database writer.

The disposable cross-site application-promotion run passed with home epoch 1
replicated to Oracle, Oracle acquiring epoch 2 after home lease expiry, and
Oracle rejecting the stale home token. The replication slot and temporary
namespaces were removed. The command reported PostgreSQL promotion and old
database-writer fencing as deliberately unproven; those remain controller
gates.

The split UI capacity has also been validated on Oracle ARM64 using candidate
images from PantryBot commit `46fc4f3`: the public commands site and private UI
each reached 2/2 Ready replicas. Direct checks of `/ready` and the public
commands API passed, as did `/mod/` on the private UI. The UI Deployments are
stateless and may remain available on Oracle, but the API/gateway/worker/
dispatcher roles remain gated on shared database, Twitch credentials, and
promotion authority bootstrap.

## Return from Oracle to home

1. Verify home database recovery is current and has caught up to the promoted
   primary.
2. Confirm home can reject writes while Oracle remains primary.
3. Promote home through the same fenced database procedure and record the next
   epoch.
4. Move gateway and dispatcher ownership only after the new primary is ready.
5. Verify Oracle stale-fence rejection, then route public/API traffic home.
6. Keep Oracle capacity available until post-return queue, outbox, and external
   checks are green.

## Failure tests required before automatic failover

- process crash of gateway, worker, and each dispatcher lane;
- home node loss with chasebot remaining available;
- complete home-site loss with Oracle application capacity available;
- asymmetric partition where both sites can reach some but not all peers;
- database primary loss and standby promotion;
- stale database writer commit attempt;
- stale gateway/dispatcher send attempt;
- duplicate event delivery and duplicate outbox claim;
- Oracle failure and controlled return to home;
- public route failure while both application sites remain healthy.

Automatic routing or database promotion remains disabled until each test has
an attached result containing the exact failure injection, detection time,
fencing proof, RTO, RPO, and rollback result.

The PantryBot branch also contains a provider-neutral failover controller
contract. Its required order is: acquire a neutral witness authority token,
fence the old database writer, promote the target database, verify database
writability, verify application readiness, and only then route traffic. The
controller deliberately has no built-in health detector, SSH, DNS, or
PostgreSQL promotion command; those must be supplied by adapters that can
prove fencing even when the old site is unreachable. This is an executable
ordering guard, not evidence that a witness or provider adapter is configured.

`scripts/pantrybot-auto-failover-oracle.sh` supplies the conservative detector
boundary for Oracle. It requires a private, site-specific home-primary probe,
three-or-more operator-selected consecutive failures, and the coupled
promotion command with both home fence adapters. It locks concurrent runs and
fails closed when the probe recovers. It must not use a public hostname, since
active-active Cloudflare routing can keep public traffic healthy while home is
down. The contract test is
`tests/pantrybot-auto-failover-gate-test.sh`. The timer and private probe are
deployed, but a real home-loss promotion and the full failure matrix below are
still required before automatic promotion is considered proven.

GCP is now a verified lightweight external observer and fencing relay: OS Login
SSH works as `chasepdrsn_gmail_com`, passwordless sudo is available for the
monitor unit, and the persisted monitor state is healthy for the public and
protected routes. The 969 MiB VM remains too small for PostgreSQL or k3s; it is
not a database or application site and must not become the coordination
authority. Its limited role is external observation and the reviewed fencing
transport, subject to the account allowance and network-impact guardrails.

## Free-cost guardrails

- Do not add a paid load balancer or cross-region database service.
- Do not assume Oracle egress is free for sustained Twitch or Discord media.
- Keep GCP observer/coordination use within the verified account allowance;
  account-level billing and quota evidence is still required.
- Keep R2 backup bytes, request volume, and retention within the documented
  free limits.
- Stop a rehearsal before any resource resize, quota increase, or paid route.
