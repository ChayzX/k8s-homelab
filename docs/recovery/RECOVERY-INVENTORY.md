# Recovery Inventory

**Status:** inventory started 2026-09-08. This file records recovery coverage without storing secret values.

## Cluster and datastore

| Item | Current evidence | Recovery state | Required proof |
|---|---|---|---|
| k3s datastore | `minecraftmachine` is the only server; k3s service has no datastore flags and logs use `kine.sock`, confirming the default SQLite/Kine backend; path is root-only `server/db/state.db` | Encrypted backup and full isolated server restore passed | Maintain the encrypted artifact and repeat the isolated rehearsal after backup rotation |
| k3s server token | Root-only `server/token`; required for datastore restoration | Encrypted with the datastore and verified in the full isolated restore | Keep the matching token with the encrypted artifact and test it only on an isolated destination |
| Kubernetes manifests | Repository manifests exist, but some live images/configuration are workflow-mutated | Partial | Reconcile live images/configuration with checked-in sources |
| Kubernetes Secrets | Names inventoried; values are not tracked | Partial | Prove reconstruction/restore of every live credential; PantryBot HA additionally requires `pantry-bot-platform` and `pantry-bot-witness` in each site namespace |
| PVC data | All listed PVCs use `local-path` and are tower-bound | Local-only | Per-application backup and isolated restore |

## Persistent data

| Namespace/data | PVC or source | Current risk | Required recovery test |
|---|---|---|---|
| `auth` PostgreSQL | `data-auth-postgresql-0`, 10Gi local-path | Authentik and dashboard unavailable with tower | Restore dump into isolated Postgres and verify Authentik startup |
| `cartwise` PostgreSQL | `cartwise-pgdata`, 2Gi local-path | Local state; Cartwise HA gate is tracked separately | Confirm backup/restore or controlled promotion, and remove the web hostPath before any Oracle placement (homelab #205) |
| `jmusicbot` | `jmusicbot-config`, R2 file sync | Mutable mirror, no generation/freshness gate | Restore a retained generation and verify token/config integrity |
| `jmusicbot` notifier | `jmusicbot-notifier-data`, 256Mi local-path | Local-only notifier state | Determine whether state matters; restore if required |
| Minecraft | `minecraft-world`, 10Gi local-path plus host backup | Archive is now copied offsite, but isolated startup restore is not yet rehearsed | Restore world, plugins, config, and version metadata elsewhere |
| Observability | Grafana 2Gi, Loki 20Gi, Prometheus 20Gi local-path | Local dashboards/history tied to tower | Treat Grafana Cloud as external log path; document acceptable local loss |
| Operations | `operations-data`, 1Gi local-path | Dashboard data and Authentik dependency tied to tower | Restore data and validate emergency access without LDAP dependency |
| PantryBot | R2/Litestream with `emptyDir` | Possible split writer during partition | Test fencing, retained generations, and promotion |

## Live secret names

Secret values remain in Kubernetes/GitHub secret stores and must not be copied here. Current names include Authentik/Postgres, LDAP, tunnel, CI deploy, image-pull, R2/Litestream, bot credentials, RCON, Grafana Cloud, and Operations credentials. The complete names are obtainable with:

```bash
kubectl get secrets -A --no-headers | awk '{print $1 "/" $2}' | sort
```

Issue #192 is not complete recovery until a controlled workflow or documented secret-store process can reconstruct every required value.
The names-only reconciliation is maintained in
[`SECRET-RESTORATION-MATRIX.md`](SECRET-RESTORATION-MATRIX.md).

## Recovery acceptance criteria

- Backup has a timestamp, retention generation, integrity check, and independent credentials.
- Restore destination is isolated from production and cannot overwrite the source.
- Restore is tested from the retained artifact, not from a live PVC.
- Application readiness and external behavior are verified after restore.
- Recovery time and data-loss window are recorded in GitHub Issue #191.
- Standby writers remain stopped until promotion establishes exclusive write authority.

## Current backup evidence

The k3s control-plane backup gate progressed on 2026-09-10. Root verification
confirmed the SQLite datastore and matching server token exist, and
`pragma quick_check` returned `ok`. The encrypted artifact
`k3s-20260910T023902Z.tar.gz.gpg` is mode 600 on the mounted recovery volume;
its SHA-256 is
`33f062100d31cf3a233984480a0b3197d371ac55940505ae899477d0329ead74`. A
streamed download from R2 matched the local checksum. The full isolated restore
rehearsal passed on 2026-09-10 using the retained passphrase and token. It
started the matching K3s image in a disposable Docker environment, verified the
restored API and expected namespaces, and cleaned up temporary state.

On 2026-09-10, a read-only R2 listing through the existing `r2-sync` sidecar
confirmed retained objects in every required recovery prefix: the newest
PostgreSQL dump was `authentik-20260909T073001Z.dump.gz`, Operations was
`operations-20260909T074501Z.db.gz`, Minecraft was
`world-backup-20260909-090102.tar.gz`, Minecraft configuration was
`minecraft-config-20260910-012516.tar.gz`, and the JMusicBot state prefix
contained `serversettings.json` updated 2026-09-08. This verifies object
presence and current retention visibility, but it is not the independent R2
freshness-monitor gate because the credential is still a workload credential.

- A compressed custom-format Authentik/Postgres dump was created at
  `/mnt/nvme/recovery/postgresql/authentik-20260909T044050Z.dump.gz` with mode
  600. `pg_restore --list` parsed the archive successfully; no restore was
  applied to the live database.
- The current R2 credential initially rejected writes because Rclone attempted
  `CreateBucket`; using `--s3-no-check-bucket` succeeded. The compressed dump
  is now also present at
  `r2:pantry-bot-backups/recovery/auth-postgresql/authentik-20260909T044050Z.dump.gz`
  with verified size 13,727,556 bytes. The R2 write path must be retained in
  the documented backup procedure.
- The recurring implementation is `scripts/authentik-postgres-backup.sh`. It
  retains 30 days on the mounted NVMe, uploads through the R2 sidecar with
  `--s3-no-check-bucket`, verifies a remote object size, and never reads secret
  values into the host shell.
- Operations audit data is SQLite at `/data/operations.db`. A consistent
  Python `sqlite3.Connection.backup()` snapshot was verified locally and in R2
  at `recovery/operations/operations-20260909T044548Z.db.gz`; the recurring
  implementation is `scripts/operations-db-backup.sh`, scheduled daily at
  02:45 with the same 30-day local retention policy.
- Minecraft world archives are now copied to
  `r2:pantry-bot-backups/recovery/minecraft/` by
  `scripts/minecraft-offsite-backup.sh`, scheduled daily at 03:30 after the
  local world archive job. The 2026-09-09 archive uploaded successfully with
  a verified remote size of 602.713 MiB. An isolated Paper 26.2 startup using
  that archive plus sanitized non-secret runtime configuration loaded the
  world and reached the normal `Done` state. This proves world restore/startup;
  secret reconstruction and controlled promotion remain separate gates. A
  separate sanitized configuration archive is generated daily at 03:40 and
  stored under `recovery/minecraft-config/` in R2; the latest verified archive
  is `minecraft-config-20260910-012516.tar.gz`. The crontab entry is installed
  for 03:40 local; the current run was manually verified because cron was
  started after today's scheduled window.
- The recurring Authentik/Postgres dump was restored into a temporary
  namespace using an empty PostgreSQL instance, and an isolated Authentik
  2026.5.6 server reached its readiness endpoint against that restored
  database. The temporary namespace and restored data were deleted after the
  rehearsal; production Postgres, Authentik, secrets, and PVCs were not
  modified.
- The recurring Operations SQLite backup was restored into an emptyDir-backed
  temporary namespace. The pinned Operations image reached `/readyz` and
  `/livez` with HTTP 200, and SQLite integrity check returned `ok`; the
  temporary namespace was deleted afterward. The current Operations image is
  published for both `linux/amd64` and `linux/arm64`, so Oracle architecture is
  no longer the capacity blocker. Writable restore, emergency access, SQLite
  writer fencing, routing, and rollback remain separate open gates.

## Management-path remediation

The exact root-gated chasebot remediation and rollback procedure is in
`docs/recovery/CHASEBOT-MANAGEMENT-FIX.md`. The deployed supported path is the
stable LAN control-plane endpoint; the tower advertises `192.168.40.208` and
the API-server kubelet proxy passed for all three nodes before Oracle was
separated. The home cluster now has two nodes; ChaseBot does not need Tailscale
for Kubernetes management, while Oracle's independent server uses the
Tailscale overlay.

The first independent-environment migration procedure is
`docs/recovery/ORACLE-JMUSICBOT-MIGRATION.md`. It deliberately selects
JMusicBot before Opsbot or stateful services and requires writer fencing,
retained R2 state, independent Oracle k3s, and a tested rollback. On
2026-09-10 Oracle was converted from a home-cluster agent into an independent
single-server k3s standby. Its JMusicBot Deployment is installed with zero
replicas, so the home cluster remains the sole Discord writer; an isolated R2
restore Job recovered the two retained state files without starting the bot.
