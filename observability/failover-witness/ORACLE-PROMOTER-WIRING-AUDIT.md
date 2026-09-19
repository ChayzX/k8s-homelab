# Oracle promoter wiring audit

This audit records the Oracle host wiring on 2026-09-19. The obsolete GCP-only
drop-in was removed and systemd was reloaded; the promoter remains disabled and
inactive. No application service was restarted.

## Installed state

The intended unit is `pantry-postgres-oracle-promoter.service`. It is loaded
but disabled and inactive. The legacy
`authentik-postgres-oracle-promoter.service` is also disabled and inactive and
must not be used for PantryBot promotion.

The installed environment contains `WITNESS_URL`, `WITNESS_SHARED_SECRET`, and
all five required hook commands. The obsolete drop-in that overrode the
old-writer command with a GCP-only fence has been moved aside. The live unit
also has the journal's writable `StateDirectory`/`ReadWritePaths` settings. The promoter is
still intentionally disabled until positive rehearsal evidence exists:

| Hook | Current evidence | Required before enablement |
|---|---|---|
| `OLD_WRITER_FENCE_COMMAND` | `/usr/local/lib/failover-witness/fence-old-writers-from-oracle.sh --confirm`; dry-run transport checks pass, positive rehearsal remains unproven | Fence Canada and home, verify database and side-effect lanes are stopped |
| `LOCAL_WRITER_FENCE_COMMAND` | `/usr/local/lib/failover-witness/fence-pantry-postgres.sh --confirm`; deployed local topology still requires validation | Fence Oracle PantryBot writers and routes, then verify |
| `REPLICATION_CHECK_COMMAND` | `/usr/local/lib/failover-witness/oracle-replication-check.sh` | Verify system identity, timeline, replay freshness, schema, and approved RPO |
| `SERVICE_CHECK_COMMAND` | `/usr/local/lib/failover-witness/oracle-service-check.sh` | Verify local database authority, all roles, readiness, and commands URL |
| `PUBLISH_ROUTES_COMMAND` | `/usr/local/lib/failover-witness/publish-cloudflare-routes.sh` | Invoke the Cloudflare route adapter and verify public origin markers |

The installed promoter remains unable to start a production election by policy:
the unit is disabled/inactive, and the controller still rejects startup before
acquiring a lease when any required hook is empty. This is the safe failure
mode until the positive fence and isolated end-to-end rehearsal gates pass.

## Exact completion work

1. Install root-owned, mode `0700` hook scripts and a mode `0600` environment
   file on Oracle; do not put secrets in Git.
2. Replace the home-only old-writer command with a positively verified fence
   covering every possible Canada/home writer and outbound dispatcher.
3. Implement and rehearse the four missing hooks against disposable resources.
4. Run the documented isolated failover acceptance test, including a lost
   witness connection and a stale writer attempt.
5. Only after those gates pass, enable the PantryBot unit and observe several
   lease renewals before any production cutover.

The repository service example now names all five required variables so an
installation cannot be mistaken for a complete wiring.
