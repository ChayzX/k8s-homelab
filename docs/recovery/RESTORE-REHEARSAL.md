# Restore Rehearsal Runbook

This runbook is a gate for moving services between home and cloud. It is intentionally a procedure outline until privileged backup paths are verified. It must never be run against production destinations.

The repeatable PantryBot rehearsal manifest is
`docs/recovery/pantry-litestream-restore-job.yaml`. Apply it manually, wait for
the generated Job to succeed, record its checksum, and inspect its logs. It
uses emptyDir only and has no production PVC mount.

## Common controls

1. Create an isolated namespace, cluster, or host with a separate destination prefix.
2. Record source backup generation, timestamp, checksum, and expected RPO.
3. Disable all scheduled writers and external event consumers in the restore target.
4. Restore data and credentials from retained artifacts.
5. Start the application with production ingress disabled.
6. Verify readiness, logs, migrations, file/database integrity, and health endpoints.
7. Run a read/write smoke test using synthetic identifiers only.
8. Destroy the isolated target and record results in Issue #191.

## Required rehearsals

| Target | Restore source | Success condition |
|---|---|---|
| k3s control plane | Datastore snapshot plus matching server token | API starts and expected namespaces/resources are present |
| Authentik/Postgres | Versioned database dump and required secrets | Authentik starts and a non-production login works |
| Operations | Operations backup plus Authentik-independent recovery path | Isolated SQLite restore evidence passes, and a separate emergency-access check is recorded |
| Minecraft | Offsite world/config/plugin archive | Server starts on the recorded version and world loads |
| PantryBot | Retained Litestream/R2 generation | Row counts and synthetic command processing match expectations |
| JMusicBot | Retained R2 generation | Config/token files validate and bot starts without a second writer |

## Not yet proven

- Authentik-independent Operations emergency-access test; the isolated restore/readiness check below is complete, but it is not a promotion or access pass.
- Secret reconstruction and controlled Minecraft promotion from the retained
  offsite archive.
- Versioned R2 generations and writer fencing.
- Restoration of all required secret values.

## Completed evidence

- **PantryBot R2 restore:** an isolated temporary pod using the production
  Litestream configuration and R2 credentials restored generation
  `82f403b823607a0c` through WAL index 70 into an empty destination. The
  restore completed successfully with a 512 MiB limit and
  `-parallelism 1`; the resulting database was 475,136 bytes with a recorded
  SHA-256 checksum. The temporary pod and restored file were deleted.
- **Production sidecar resource finding:** running the same restore inside the
  64 MiB production Litestream sidecar was killed with exit 137 around WAL
  index 39, including at `-parallelism 1`. This is a recovery-capacity issue,
  not evidence of a bad R2 backup. Any future restore-on-start path must have
  an explicit memory budget validated against a full generation.
- **JMusicBot R2 file restore:** an isolated copy from the R2 prefix restored
  `serversettings.json` and `youtubetoken.txt` into a temporary sidecar
  directory with matching ownership and recorded checksums. The temporary
  directory was deleted after verification. This proves object retrieval, not
  complete bot startup or writer fencing.

- **Authentik/Postgres restore:** the recurring dump
  `authentik-20260909T044400Z.dump.gz` was copied into a temporary namespace,
  restored into an empty PostgreSQL 17.10 instance with `pg_restore`, and used
  by an isolated Authentik 2026.5.6 server. The server reached
  `/-/health/ready/` with HTTP 200. No ingress, production PVC, or production
  service was used. The temporary namespace was deleted after verification.
  A repeat on 2026-09-11 used the retained
  `authentik-20260909T044050Z.dump.gz` artifact and copied configuration
  Secret names/values into a disposable namespace. PostgreSQL restore passed,
  Authentik reached readiness, and its API returned the restored 12-user
  dataset. The actual flow accepted a temporary synthetic username/password
  and advanced to the restored WebAuthn-registration stage. This proves
  restored identity data and password-stage behavior; full session completion
  remains open until a non-production WebAuthn credential or an explicitly
  configured alternate test flow is exercised. The namespace and synthetic
  user were deleted after the rehearsal.
  Before the interactive step, run
  `AUTHENTIK_RESTORE_NAMESPACE=<namespace> scripts/authentik-restore-contract-check.sh`.
  It refuses `auth`, checks that the disposable PostgreSQL, Authentik, and
  LDAP workloads are ready, and verifies only the required Secret key names. Its
  `provider_behavior=follow_up_required` and
  `session_completion=follow_up_required` markers prevent the preflight from
  being mistaken for a completed login or provider reconstruction.

- **Operations isolated SQLite restore:** `operations-20260909T044548Z.db.gz`
  was restored by an init container into an emptyDir-backed temporary
  deployment. The pinned Operations image reached `/readyz` and `/livez` with
  HTTP 200, and SQLite's `integrity_check` returned `ok` with five tables
  present. The destination was local to the temporary target; it did not use
  the production PVC, production Service, or production ingress. The
  temporary namespace was deleted after verification. This passes the
  isolated restore/readiness check only. The rehearsal had to be pinned to an
  AMD64 node because the current image has no ARM64 manifest; this is a
  required constraint for any Oracle migration until the image is rebuilt
  multi-architecture.

- **Minecraft restore/startup:** the retained
  `world-backup-20260909-090102.tar.gz` archive was extracted into an emptyDir
  on the AMD64 tower node. Sanitized non-secret runtime configuration was
  supplied separately; RCON passwords, management keys, and the Floodgate
  key were excluded. Paper 26.2 loaded the restored world, started Geyser,
  and reached the normal `Done` log line. The temporary namespace was deleted
  after verification. The resulting archive is also present in R2 at
  `recovery/minecraft/world-backup-20260909-090102.tar.gz` with verified size
  602.713 MiB. This proves world restore/startup, not secret reconstruction
  or promotion of a second writer.

## Authentik/Postgres backup currently available

The 2026-09-09 dump is stored locally at
`/mnt/nvme/recovery/postgresql/authentik-20260909T044050Z.dump.gz` and in R2 at
`r2:pantry-bot-backups/recovery/auth-postgresql/authentik-20260909T044050Z.dump.gz`.
The R2 upload requires Rclone's `--s3-no-check-bucket` option because the
scoped credential may write the existing bucket but may not create buckets.
An R2 download was verified at 13,727,556 bytes with SHA-256
`351e38304f1ae37a7994420c7302dd3f702f1b57cf9fa6e71823eeda3537b7b2`.
The recurring source backup is `scripts/authentik-postgres-backup.sh`,
scheduled daily at 02:30 in the tower user's crontab.
Restore procedure:

1. Download the selected R2 object to an isolated host.
2. Decompress it into a temporary restore directory.
3. Run `pg_restore --list` and record the TOC/checksum.
4. Restore into an isolated Postgres instance, never the production service.
5. Start a matching Authentik instance with restored secrets and verify its
   readiness endpoint; the 2026-09-09 rehearsal passed this gate. A complete
   non-production interactive login remains a separate stronger test.

Operations backups use SQLite's online backup API through
`scripts/operations-db-backup.sh`, scheduled daily at 02:45. The current R2
artifact is `recovery/operations/operations-20260909T044548Z.db.gz`.

### Operations acceptance evidence

Record the following as two separate checks in Issue #201. Neither check
requires PostgreSQL promotion or fencing, and neither authorizes two writable
SQLite sites.

1. **Isolated SQLite restore:** record the selected R2 object name, generation
   timestamp, byte size, SHA-256, restore target/namespace, image digest, and
   node architecture. Restore to a new local path or `emptyDir`, never over
   the source file or a production PVC. Capture `PRAGMA integrity_check`, the
   expected table count, `/livez`, and `/readyz`; readiness must be checked
   against the restored copy, not merely the image. Record that production
   Services, ingress, scheduled writers, and external event consumers were not
   enabled. This is a restore/readiness pass, not proof of emergency access,
   writable promotion, routing, RTO/RPO, or rollback.
2. **Authentik-independent emergency access:** in the same isolated target or
   a separately controlled recovery target, make Authentik and LDAP
   unavailable without changing production. From the pre-provisioned owner
   recovery path (for example, direct host SSH plus a narrowly scoped
   Kubernetes credential or local port-forward), prove that the operator can
   reach the restored target and perform only the documented recovery action.
   Record the path type, target boundary, authentication mechanism name (never
   its value), Authentik/LDAP outage observation, endpoint/status or sanitized
   command output, and cleanup result. A screenshot or HTTP 200 from
   `/livez`/`/readyz` alone is not emergency-access evidence: it must show that
   the recovery operator can reach the target without an Authentik/LDAP
   session. If the path cannot perform an authorized dashboard read or
   mutation, record that limitation and keep the emergency-access gate open.

The current Operations evidence above passes check 1 only. Check 2, local
writable promotion, single-writer fencing, routing, measured RTO/RPO, and
rollback remain open in Issue #201.

## Minecraft offsite archive currently available

The local world archive job produces a consistent tarball under
`/home/chase/minecraft-backups`. `scripts/minecraft-offsite-backup.sh` copies
the newest gzip archive to
`r2:pantry-bot-backups/recovery/minecraft/` at 03:30 daily, after the local
03:00 archive job. The 2026-09-09 archive was uploaded successfully and its remote size was
  verified. The archive contains the world backup produced by the existing
  updater; the isolated extraction and Paper startup test passed when paired
  with sanitized non-secret runtime configuration. RCON, management, and
  Floodgate secrets are intentionally excluded and must be reconstructed from
  the secret inventory before a promoted server can accept players.

The companion script `scripts/minecraft-config-offsite-backup.sh` creates the
sanitized configuration archive after the world upload. It excludes RCON and
management credentials and the Floodgate key, verifies the compressed archive,
and uploads it to `recovery/minecraft-config/` in R2. The latest verified
archive is `minecraft-config-20260910-012516.tar.gz`; its contents passed the
sensitive-filename and sanitized-property checks. A future full promotion
must restore both archives and separately provision the excluded secrets.
