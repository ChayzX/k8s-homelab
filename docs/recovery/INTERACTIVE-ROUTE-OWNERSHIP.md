# Interactive route ownership

Normal owner: Home

Promotion owner: Oracle

- `auth.greeniespantry.uk`: Home
- `oauth.greeniespantry.uk`: Home
- `grafana.greeniespantry.uk`: Home
- `operations.greeniespantry.uk`: Home

The stateful interactive services have one normal public site. Cloudflare
routes for `auth.greeniespantry.uk`, `oauth.greeniespantry.uk`,
`grafana.greeniespantry.uk`, and `operations.greeniespantry.uk` must terminate
at Home origins while Home PostgreSQL is authoritative. Oracle application
capacity may remain warm, but it is not an equal public origin while its
Authentik database is remote or read-only.

During controlled promotion, fence Home first, promote Oracle Authentik
PostgreSQL, deploy `auth/ORACLE-FAILOVER-VALUES.yaml`, verify Authentik
readiness and `pg_is_in_recovery() = false`, and only then switch these four
routes to Oracle. The reverse sequence is required for failback.

PantryBot's OAuth-free `commands.greeniespantry.uk` route is independent and
must not be changed by this ownership rule.
