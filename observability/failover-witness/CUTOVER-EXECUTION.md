# Controlled Canada cutover

This runbook is for a planned maintenance cutover. It deliberately stops the
currently active site, so run it only after the streaming owner has approved
the window. The Oracle promoter remains disabled until the rehearsal gates
below pass.

## Sequence

1. Record the current witness epoch, Canada container list, PostgreSQL system
   identifier, and public route origin markers.
2. Acquire the planned promotion lease from the witness and keep renewing it.
3. Run `fence-canada-from-oracle.sh`; require a positive result that every
   mutating Canada container and PostgreSQL process is stopped.
4. Run `oracle-replication-check.sh`; require standby mode, matching system
   identifier, and received WAL fully replayed.
5. Promote the Oracle standby and verify `pg_is_in_recovery() = false`.
6. Start Oracle’s seven application roles and wait for their private readiness
   checks. Do not use a public tunnel health check as an authority check.
7. Run `publish-cloudflare-routes.sh` with `PANTRY_PROMOTION_SITE=oracle` and
   verify every public response identifies Oracle.
8. Run the delegated regression suite and record its result.

## Abort and rollback

Abort and fence Oracle immediately if any step fails, the lease cannot renew,
replication identity differs, or a route exposes the wrong origin. Restore
Canada only after Oracle is fenced and its database is re-seeded or rewound to
the authoritative timeline; then start Canada, publish Canada routes, and run
the regression suite again. Never start both writers to "see which one wins."

## Evidence required for completion

- Positive Canada fence result, including no surviving PostgreSQL process.
- Oracle promotion and local service checks pass.
- Public routes identify the promoted site and all standby origins are absent.
- Delegated regression report is recorded.
- Canada is restored as the final active site with PostgreSQL primary state and
  all eight containers healthy.
