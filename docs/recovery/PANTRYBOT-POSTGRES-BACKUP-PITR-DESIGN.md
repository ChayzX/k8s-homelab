# PantryBot PostgreSQL Backup and PITR — Design Note (#311)

**Status:** design only. No manifests, no apply, no deploy. Written 2026-09-24.

**Evidence basis:** manifests and scripts in `k8s-homelab` and `pantry-bot` ONLY.
The only kubectl context on this machine is `docker-desktop`; there is no
`psql` binary. Nothing here was measured against the live cluster. Every
number marked *(unmeasured)* must be confirmed before implementation.

---

## 1. Corrections to the working assumptions

**1.1 "No PostgreSQL backup exists anywhere" is true for PantryBot and false for
the fleet.** `scripts/authentik-postgres-backup.sh` is a working, scheduled,
rehearsed PostgreSQL backup: it discovers the Ready pod labeled
`authentik.postgres/role=primary`, streams `pg_dump --format=custom`, gzips,
verifies with `gzip -t` and a size floor, writes to `/mnt/nvme/recovery/postgresql`,
then transfers to `r2:pantry-bot-backups/recovery/auth-postgresql/` via the
`r2-sync` sidecar with an rclone hash check *and* an exact remote byte-count check.
It runs from a MinecraftMachine crontab at 02:30 under `sudo -n`.
`RECOVERY-INVENTORY.md` records a successful production run on 2026-09-20
(`authentik-20260919T222509Z.dump.gz`, 47,137,647 bytes) and a `pg_restore` into a
disposable namespace on 2026-09-19, after which an isolated Authentik reached
readiness. **Do not design this from zero. Copy the proven one.**

**1.2 The object store should be Cloudflare R2, not Oracle Object Storage.**
R2 bucket `pantry-bot-backups`, endpoint
`https://0d22ee71e5473142b1678cb7005f80f8.r2.cloudflarestorage.com`, is already the
fleet's recovery store for Authentik, Operations, Minecraft, Minecraft config and
JMusicBot state. `FREE-TIER-CAPACITY-BASELINE.md` records the R2 free tier as
10 GB-month, 1M Class A ops, 10M Class B ops, **free egress**. See §6 for the
reasoning against OCI Object Storage, and §7 for the condition that must be met
first.

**1.3 "The only backup credential is pantry-bot-litestream" is incomplete.**
`jmusicbot-r2` in the `jmusicbot` namespace holds a copy of the same R2
credentials. Separately, `observability/external-monitor/README.md` specifies a
*read-only* monitor credential (`MONITOR_R2_ACCESS_KEY_ID` /
`MONITOR_R2_SECRET_ACCESS_KEY`) that has never been issued — that missing
credential is the single blocking prerequisite for §5.

**1.4 The checked-in manifest for the live production primary would destroy it.**
`observability/failover-witness/home/postgres-authority-standby-home-canada.yaml`
is the StatefulSet named in the migration as the real primary, and it is committed
with `replicas: 0` and a comment: *"Never re-apply this file to a live standby (it
would scale it to 0); patch instead."* That file is in the restore path. Under
incident pressure, someone will `kubectl apply -f` it. This is the most dangerous
artifact in the repository and it is adjacent to this work.

**1.5 The Oracle standby StatefulSet is not in the repository.** Only
`observability/failover-witness/oracle/oracle-reseed-from-home.yaml` exists, a
one-shot `pg_basebackup` Pod that references PVC
`data-postgres-authority-standby-oracle-v2-0` and replicates from
`100.84.89.87:5432` using slot `pantry_oracle_standby` as user `pantry_replicator`.
The workload the entire cutover depends on has no committed manifest. Backup
design cannot compensate for that.

**1.6 A CronJob precedent already exists and is the right shape.**
`pantry-bot/k8s/ha/base/postgres-analyze-cronjob.yaml` runs weekly, reads
`PANTRY_DATABASE_URL` from the `pantry-bot-platform` secret — explicitly so it
"follows the writer wherever a promotion puts it" — and uses `postgres:17.10-bookworm`.
It lives in `base`, so it is deployed to both sites by the `home` and `oracle`
overlays. Use exactly this pattern.

---

## 2. Established facts

### How Postgres is deployed

| Property | Value | Source |
|---|---|---|
| Live primary workload | StatefulSet `postgres-authority-standby-home-canada`, ns `pantry-bot` | `observability/failover-witness/home/postgres-authority-standby-home-canada.yaml` |
| Image | `postgres:16-alpine@sha256:cf78e766…` | same |
| Storage | `volumeClaimTemplates`, `storageClassName: local-path`, **8Gi**, RWO | same |
| PVC retention | `whenDeleted: Retain, whenScaled: Retain` | same |
| Network | `hostNetwork: true`, `listen_addresses=127.0.0.1,100.84.89.87` | same |
| Placement | `nodeSelector kubernetes.io/hostname: minecraftmachine` | same |
| Server args | `hot_standby=on`, `wal_receiver_timeout=10s`, `listen_addresses=…` | same |
| Oracle standby PVC | `data-postgres-authority-standby-oracle-v2-0` | `oracle/oracle-reseed-from-home.yaml` |
| Replication slot | `pantry_oracle_standby`, user `pantry_replicator`, app name `pantry-oracle-standby` | same |

The WAL prerequisites (`wal_level=replica`, `max_wal_senders=10`,
`max_replication_slots=10`, `wal_keep_size=256MB`, `max_slot_wal_keep_size=1GB`)
are set on the `postgres-authority` StatefulSet spec
(`docs/recovery/pantrybot-postgres-authority-production.yaml`), which is scaled to
zero. The live primary's PGDATA was seeded from that lineage by `pg_basebackup`, so
it inherits those values in `postgresql.conf`.

### The single most important configuration fact

**`archive_mode` is not set anywhere in the repository.** No manifest, no configmap,
no `postgresql.auto.conf` fragment sets it. The live primary's args do not set it.

`archive_mode` is a postmaster-level (`PGC_POSTMASTER`) setting: **it cannot be
changed without restarting PostgreSQL.** Every `archive_command`-based design
(pgBackRest push, WAL-G push) therefore requires a restart of production Postgres.
This drives the whole recommendation — see §4 and §8.

*(unmeasured: confirm with `SHOW archive_mode;` and `SHOW archive_command;` against
the live primary. If someone already set it in PGDATA, half of §8 disappears.)*

### Schema

44 distinct tables in the PostgreSQL schema
(`src/platform/schema.ts`, `src/platform/domainSchema.ts`). The analyze CronJob
comment says 45 and that 43 of them had no planner statistics; treat 45 as the
operational count.

Workload shape is a Twitch/YouTube chat bot: `users`, `watchtime`, `pantry_points`,
`inventory`, `engagement_message_counts`, `boss_*`, `giveaway_*`, plus a
transactional outbox.

**`pantry_outbox` has no pruning.** It is `outbox_id TEXT PRIMARY KEY` with a
`JSONB payload`, a unique `idempotency_key`, a `status` lifecycle
(`pending → processing → completed`), an `attempts` counter and
`locked_by`/`locked_until`. Grepping `src/` finds no `DELETE FROM pantry_outbox`
and no retention job. So every external side effect writes one row and then
**updates it at least twice** — an insert plus two heap updates plus index
maintenance on `pantry_outbox_claim_idx`, and the table grows monotonically.
This is the dominant WAL generator and the dominant growth term in the database.
It is also the reason a WAL-volume estimate cannot be borrowed from a generic
chat-bot workload.

### Size and WAL volume — NOT measured

I could not reach the cluster. Bounds from what is committed:

- Database is smaller than **8Gi** (the PVC has never been resized) and fits
  alongside everything else in Oracle's **33 GiB free root**
  (`FREE-TIER-CAPACITY-BASELINE.md`, 2026-09-13).
- For comparison, the Authentik dump is **47 MB** compressed custom-format.
- WAL rate is low enough that a standby over a 129 ms Tailscale link holds
  ~0.79 s replay lag and has not been evicted by `max_slot_wal_keep_size=1GB`.
  A slot cap of 1 GB is not survivable for a high-WAL database on a link that
  flaps, so WAL is plausibly **O(100 MB – 1 GB/day)** *(unmeasured)*.

**Measure before implementing.** Three read-only queries, five minutes:

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total, n_live_tup
  FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;
-- WAL/day: sample twice, 1h apart, on the PRIMARY
SELECT pg_current_wal_lsn();
```
`pg_wal_lsn_diff` between the two samples × 24 gives bytes/day. Also
`SELECT count(*), pg_size_pretty(pg_total_relation_size('pantry_outbox')) FROM pantry_outbox;`
— if that is large, fix retention before designing around it.

---

## 3. Options evaluated

Common assumption: database ≲ 1 GB, WAL ≲ 1 GB/day *(unmeasured)*.

### A. pgBackRest → object storage

- **RPO:** `archive_timeout=60s` forces a segment switch each minute, so ~60 s.
  Without `archive_timeout`, RPO is "until the next 16 MB segment fills", which on a
  quiet bot could be hours. `archive_timeout` is mandatory, not optional.
- **Restore wall-clock:** `pgbackrest restore` of a sub-GB repo, plus replay of
  WAL since the last backup. Single-digit minutes to the point PostgreSQL accepts
  connections, on either site. Delta restore makes repeated rehearsals cheap.
- **Cost:** free within the object-storage allowance. Class A ops ≈ 1,440 WAL
  PUTs/day + backup parts ≈ 45–60k/month against R2's 1M — not a constraint.
- **What breaks it:** (i) needs `archive_mode=on` → **a production Postgres restart**;
  (ii) pgBackRest must run on the same host/namespace as PGDATA, so it is a sidecar
  in the StatefulSet — editing the StatefulSet is another restart, so fold it into
  the same one; (iii) no official multi-arch pgBackRest image, so Oracle ARM64 needs
  `apk add pgbackrest` in an alpine base or a self-built image; (iv) if
  `archive_command` starts failing, `pg_wal` grows without bound and **eventually
  takes the primary down** — loud, but loud in the worst possible way.
- **Silent-failure detection:** best in class. `pgbackrest check` forces a WAL
  segment switch and verifies the segment actually landed in the repo — one command
  that proves `archive_command`, credentials and repo write together.
  `pgbackrest info --output=json` gives newest-backup and newest-WAL timestamps for
  threshold assertions.

### B. WAL-G → object storage

Same RPO, same restore profile, same `archive_mode` restart requirement, same
sidecar requirement. Slightly easier ARM story (upstream ships linux-arm64 release
binaries). **Materially worse verification:** `wal-g backup-list` exists, but there
is no equivalent of `pgbackrest check`, no built-in "force a segment and prove it
arrived". Given that §5 is the part of this design that actually matters, that
single gap is disqualifying.

### C. Hand-rolled WAL archiving + periodic `pg_basebackup`

This is options A/B with the retention manager, the repo manifest, the integrity
checks and the verification tooling removed and replaced with shell you maintain.
It carries every constraint of A and none of its safety. **Rejected.**

Worth noting the variant the task did not list: **`pg_receivewal` against a
replication slot.** It streams WAL continuously to a directory, needs **no
`archive_mode`, no primary restart and no StatefulSet edit** — only a new slot
(`max_replication_slots=10`, so there is headroom). Its weakness is that it
produces a pile of segments with no base backup, no manifest and no retention, so
it is not a backup by itself. It is, however, the correct **bridge** while the
restart in §8 is scheduled.

### D. Scheduled `pg_dump`

- **RPO:** the schedule. 24 h at the existing 02:30 cadence; 6 h if run four times
  a day. **No PITR at all** — you cannot recover to a point between dumps.
- **Restore wall-clock:** `pg_restore` of a sub-GB custom-format dump: a few
  minutes. **Plus a mandatory `ANALYZE`** — `postgres-analyze-cronjob.yaml`
  documents that a freshly restored or freshly seeded database starts with zero
  planner statistics and that on Authentik this cost 1.4 s per login step instead of
  13 ms. A restore that skips `ANALYZE` comes up "working" and catastrophically slow.
- **Cost:** free.
- **What breaks it:** a dump that exits 0 while producing something unrestorable;
  a long dump holding a transaction open against a bloating `pantry_outbox`.
- **Silent-failure detection:** already solved in this fleet — size floor,
  `gzip -t`, rclone hash check, exact remote byte-count check, all in the Authentik
  script.
- **Verdict:** does not satisfy "continuous backup", but it is the fastest thing
  that closes the current hole today, and it is the only option that needs **no
  restart of production**. See §8 step 0.

### E. Stream a copy back to Home

- **RPO:** ~1 s, matching the current Oracle replica.
- **Restore wall-clock:** the best of any option — it is a promote, not a restore.
- **Cost:** free. The manifests largely exist
  (`home-reseed-from-canada.yaml`, `pantrybot-postgres-standby-home-failback.yaml`).
- **What breaks it:** **it is not a backup.** It replicates a `DROP TABLE`, a bad
  migration, an application bug and block corruption with ~1 s of fidelity. For the
  single most likely data-loss event here — a logical mistake, not a site fire — it
  provides no recovery point whatsoever. Also: a standby stopped for longer than
  `max_slot_wal_keep_size=1GB` of primary WAL silently falls off the slot and needs
  a full reseed, and it contradicts the decided "nothing running at Home" posture.
- **Verdict:** excellent for RTO, **unacceptable as the only backup**. Not a
  substitute for A. Reconsider only as an RTO accelerator after A exists.

---

## 4. Recommendation

**pgBackRest, repository on Cloudflare R2, `archive_timeout=60s`, configured on
Oracle during the cutover restart — preceded immediately by a verified `pg_dump`
of the live Home primary, today.**

Reasoning:

1. **Only A and B provide PITR, and A's verification is decisively better.** The
   task's own framing — "a backup nobody verifies is not a backup" — is the
   tiebreaker. `pgbackrest check` proves the archive path end to end in one exit
   code. WAL-G has no equivalent.
2. **The restart is free exactly once, and that moment is the cutover.** Promotion
   itself does not restart the postmaster, but the cutover is already an agreed
   maintenance window with the app down or draining. Setting `archive_mode=on` and
   adding the sidecar during that window costs nothing extra. Miss it and you buy a
   second production outage later, for a project whose whole point was to stop
   having those.
3. **R2 over OCI Object Storage** — see §6.
4. **`pg_dump` first, because the risk is now.** Right now, Home is the only
   writable copy of production and there is no backup of it. The moment Oracle is
   promoted, Home's data becomes a diverged timeline of decreasing value. A
   verified dump of the live primary is ~30 minutes of work, reuses a proven script,
   needs no restart, and closes the largest single risk in the migration before any
   of the pgBackRest work starts. Keep it running afterwards as an independent
   second recovery path — two mechanisms that fail differently is the cheapest real
   redundancy available.

---

## 5. How you would know it had silently stopped

Four layers. The ordering matters: only one of them survives the failure that
matters most.

**Layer 1 — in-band, proves the archive path.** CronJob running
`pgbackrest check --stanza=pantrybot` every few hours. It forces a WAL segment
switch and confirms the segment arrived in the repo, so it exercises
`archive_command`, credentials and repo write in a single command. Non-zero exit is
the signal.

**Layer 2 — out-of-band, proves freshness from outside both sites. This is the
one that counts.** `observability/external-monitor/README.md` already specifies
exactly this and it is already deployed on GCP:

```
MONITOR_R2_PREFIXES=auth=recovery/auth-postgresql/:172800,…
```

Add `pantrybot=recovery/pantrybot/:<max_age_seconds>`. Per that README, when
`MONITOR_R2_PREFIXES` is set, "incomplete credentials or a stale/missing prefix
fails the monitor". Per `ALERT-EVALUATION-CONTRACT.md` the GCP monitor is a
singleton evaluator with a non-blocking advisory lock, a durable JSON alert ledger,
per-check identity `external-monitor:<check>`, a three-consecutive-failure
threshold, and bounded provider-acceptance receipts checkable with
`probe-notification-receipt.py`.

**This layer is the only one that still works when Oracle is gone — which is
precisely when you need to be told the backup is stale.** Layers 1 and 3 run inside
the cluster being backed up and die with it.

**It is currently disabled.** The README says R2 checks "remain disabled until" a
read-only credential exists. **Issuing a read-only, bucket-scoped R2 token is the
single blocking prerequisite of this entire design, and it is free.** Do it first.

**Layer 3 — repo-state assertion.** Daily CronJob on `pgbackrest info --output=json`
asserting newest full backup age, newest differential age and newest archived WAL
age against explicit thresholds, exiting non-zero otherwise. Catches "backups are
running but the last full is 40 days old", which layer 2's freshness-by-prefix check
can miss when differentials keep the prefix looking fresh.

**Layer 4 — restore rehearsal.** Quarterly `pgbackrest restore` into a disposable
namespace, following the documented Authentik precedent exactly: isolated namespace,
verify the app reaches readiness against the restored database, capture evidence,
delete the namespace. `RECOVERY-INVENTORY.md`'s acceptance criteria already require
"restore is tested from the retained artifact, not from a live PVC". **Include
`ANALYZE` in the rehearsal and time it**, because it is in the real restore path
(§3D) and it is currently invisible.

A backup not restored this quarter is a hypothesis.

Note honestly: there are **no Prometheus alert rules and no Alertmanager anywhere
in this repository** (`ALERT-EVALUATION-CONTRACT.md`). A failing CronJob produces a
failed Job object and nothing else unless the host `k3s-watcher` picks it up. Layers
1 and 3 need a named evaluator or they are only evidence after the fact.

---

## 6. Why R2 and not Oracle Object Storage

The task proposed OCI Object Storage's 20 GB Always Free. Against it:

1. **Co-location with the only serving site.** After consolidation Oracle is the
   single serving site. Putting its only backup in the same tenancy and region means
   one tenancy-level event — including Always Free idle reclamation, which
   `FREE-TIER-CAPACITY-BASELINE.md` explicitly warns about at "below 20% at the 95th
   percentile for seven days" — can take the site and its backups together. R2 is a
   third vendor, independent of both Home and Oracle.
2. **Everything already exists for R2.** Bucket, endpoint, credentials, an rclone
   transfer pattern with hash and byte-count verification, a restore rehearsal
   precedent, and a written-but-unenabled freshness monitor. For OCI every one of
   those is new work, and the freshness monitor in particular would have to be
   rebuilt from scratch.
3. **Free egress.** Restore is egress, and this design demands repeated restore
   rehearsals. R2 charges nothing for it; OCI egress draws on a shared account
   allowance that the baseline document explicitly lists as unverified.

Against R2, honestly: **10 GB versus 20 GB.** That is the real trade, and §7 is
why it is currently the binding constraint.

---

## 7. Blocking finding: R2 free-tier headroom is probably already gone

This is outside #311's scope but it invalidates the recommendation if unaddressed,
and "everything must be free" is a hard requirement.

**None of the R2 uploaders implement remote retention.** `operations-db-backup.sh`
has `RETENTION_DAYS=30` but its delete is
`find "$BACKUP_ROOT" … -mtime +30 -delete` — **local NVMe only**.
`scripts/minecraft-offsite-backup.sh` does `rclone copyto` and then only
`rclone size` to verify; it never deletes. `authentik-postgres-backup.sh` likewise
prunes locally, not remotely.

So R2 accumulates, every day, forever:

| Prefix | Per-day | Source |
|---|---|---|
| `recovery/minecraft/` | ~602.7 MiB | `RECOVERY-INVENTORY.md` (2026-09-09 archive) |
| `recovery/auth-postgresql/` | ~47 MB | `RECOVERY-INVENTORY.md` (2026-09-20 run) |
| `recovery/minecraft-config/`, `recovery/operations/`, `jmusicbot/` | small | — |

That is **~650 MB/day**. Uploads have been running since at least 2026-09-09. By
2026-09-24 that is roughly **9–10 GB — at or over the entire 10 GB free
allowance**, before PantryBot's backup writes a single byte.

**Verify immediately** (read-only, through the existing `r2-sync` sidecar, the same
path `RECOVERY-INVENTORY.md` used on 2026-09-10):

```
rclone size r2:pantry-bot-backups --config=/dev/null --s3-no-check-bucket
rclone size r2:pantry-bot-backups/recovery/minecraft/ --config=/dev/null --s3-no-check-bucket
```

Then either:

- **(preferred)** add an R2 bucket lifecycle rule expiring `recovery/minecraft/`
  and `recovery/auth-postgresql/` at 14–30 days. Free, declarative, and needed
  regardless of #311; or
- move the pgBackRest repo to OCI Object Storage's 20 GB and accept §6's
  co-location and egress trade-offs.

Do not add a growing pgBackRest repository to a bucket that is already at its
free-tier ceiling. The most likely outcome is a hard write failure at 03:00 that
nobody sees, because layer 2 is disabled.

---

## 8. Implementation sequence (design only — do not execute from this note)

0. **Today, before anything else.** Issue a read-only bucket-scoped R2 token and
   enable `MONITOR_R2_PREFIXES` (§5 layer 2). Resolve §7. Then clone
   `authentik-postgres-backup.sh` for PantryBot against the live Home primary,
   run it once, and `pg_restore` it into a disposable namespace. The largest hole
   in the migration is closed without restarting anything.
1. Measure database size, per-table sizes, `pantry_outbox` row count and WAL/day
   (§2). Confirm `SHOW archive_mode`. Size R2 retention from the real number.
2. Build a multi-arch pgBackRest sidecar image (alpine + `apk add pgbackrest`), or
   confirm an existing arm64 image. Oracle is ARM64.
3. Write the Oracle standby StatefulSet into the repository (§1.5). Backup design
   cannot proceed on an uncommitted workload.
4. **At the cutover window only:** add the pgBackRest sidecar, set `archive_mode=on`,
   `archive_command`, `archive_timeout=60s`, restart, `pgbackrest stanza-create`,
   take the first full backup, run `pgbackrest check`.
5. Add the check/info CronJobs to `pantry-bot/k8s/ha/base/`, following
   `postgres-analyze-cronjob.yaml` exactly — including sourcing
   `PANTRY_DATABASE_URL` from `pantry-bot-platform` so it follows the writer.
6. Rehearse a restore into a disposable namespace. Record wall-clock, including
   `ANALYZE`. Update `RECOVERY-INVENTORY.md`.

Keep the `pg_dump` from step 0 running permanently alongside pgBackRest.

---

## 9. What must be true for a 30-minute restore — and where it is not

**Case 1 — Oracle alive, database lost or corrupted.** Achievable, with margin.
`pgbackrest restore` of a sub-GB repo over Oracle's link plus WAL replay is
single-digit minutes; the budget is spent on decision time, not I/O. Requirements:
the PVC has space for a parallel restore path; retention still holds a backup from
before the corruption (so retention must exceed your realistic *detection* time, not
your recovery time — logical corruption is often noticed days later, which argues
for ≥14 days); `ANALYZE` runs as part of the restore.

**Case 2 — Oracle gone, restore to cold Home. This cannot make 30 minutes today,
and the backup is not the reason.** It requires *all* of:

- **(a) Every secret present at Home** — `pantry-bot-platform`,
  `pantry-bot-postgres-authority`, `pantry-bot-witness`, the tunnel token,
  `ghcr-pull-secret`, the R2 credentials. `RECOVERY-INVENTORY.md` states plainly
  that Issue #192 "is not complete recovery until a controlled workflow or
  documented secret-store process can reconstruct every required value," and tracks
  it as **Partial**. **This is the binding constraint.** You cannot reconstruct
  unknown credentials in 30 minutes at any level of preparation.
- **(b) Container images already present on the Home node.** A cold pull of
  `postgres:16-alpine` plus the PantryBot images over a residential link, during an
  incident, plausibly exceeds the entire budget on its own.
- **(c)** Manifests applied and `nodeSelector kubernetes.io/hostname: minecraftmachine`
  still satisfiable.
- **(d)** The hardcoded Tailscale listen address `100.84.89.87` still correct for
  the restored Home Postgres, and `PANTRY_DATABASE_URL` pointing at it.
- **(e)** The Cloudflare connector flip: scale Oracle's `cloudflared` to 0, **then**
  Home's to 1 — never overlapping, since both sites share one tunnel token per
  tunnel and overlapping connectors produce a silent 50/50 split rather than a
  cutover.
- **(f)** `ANALYZE` after restore, or the service returns "up" and unusably slow.

Realistically, cold Home is **45–90 minutes on a good day and unbounded on a bad
one**, dominated by (a) and (b).

**Be honest in the RTO statement.** Two options, both defensible:

1. **Declare two RTOs.** 30 minutes for database loss with Oracle alive (the more
   likely event); a separate, larger, measured RTO for total site loss. State both.
2. **Pre-stage Home to make 30 minutes reachable.** Secrets present, images
   pre-pulled, manifests applied at `replicas: 0`. This is still "a current backup
   plus manifests, nothing running, no witness, no promoter, no fencer, no automatic
   failover" — it does **not** violate the cold-target decision, because nothing
   runs and nothing decides. It converts (a) and (b) from incident work into
   preparation, which is the only way case 2 gets near 30 minutes.

What I will not do is claim 30 minutes for case 2 as things stand. The backup
design in §4 can deliver a database in minutes. The 30 minutes is lost to
unreconstructable secrets and cold images, and no backup technology fixes either.
