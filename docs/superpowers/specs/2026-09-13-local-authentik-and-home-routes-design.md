# Local Authentik and Home-Preferred Interactive Routes

## Problem

Normal interactive access currently crosses the Home/Oracle WAN boundary. The
Home Authentik Secret points at Oracle's public PostgreSQL transport
(`100.78.181.15:30434`), while public Cloudflare tunnels exist on both sites.
This makes login and protected Grafana requests sensitive to Frankfurt latency.

## Approved architecture

- Home is the normal Authentik and Grafana site.
- Home Authentik uses the local `auth-postgresql` Service and Home PostgreSQL
  remains the writable identity authority.
- Oracle keeps Authentik application capacity and a physical PostgreSQL standby
  for disaster recovery, but Oracle is not a normal public Authentik origin.
- Normal Grafana, Authentik, OAuth, Operations, and LDAP routes use the Home
  Cloudflare tunnel/origins only.
- Oracle promotion fences Home, promotes the Oracle Authentik PostgreSQL
  standby, rewrites Oracle Authentik to its local Service, and then enables the
  Oracle public routes. Failback reverses those steps after replication is
  re-established.
- PantryBot routes and workloads are not changed by this work.

## Safety invariants

1. There is never more than one writable Authentik PostgreSQL authority.
2. Oracle Authentik must not be exposed publicly while its database is a
   standby.
3. A route switch happens only after the target Authentik readiness check and
   database-role check pass.
4. The normal route change is reversible without changing PantryBot secrets or
   workloads.
5. Authentik-independent SSH and monitoring remain available during promotion.

## Acceptance evidence

- Home Authentik reports local PostgreSQL configuration by sanitized key/value
  inspection and reaches `/-/health/ready/`.
- Oracle remains a standby and has no normal public Authentik route.
- `auth.greeniespantry.uk`, `oauth.greeniespantry.uk`,
  `grafana.greeniespantry.uk`, and `operations.greeniespantry.uk` return from
  the Home origin with interactive latency measured before/after.
- Authentik login and Grafana protected access work after the local switch.
- Repository manifest/runbook tests pass and the exact rollback sequence is
  documented in GitHub issue #198 and the failover issue.
- PantryBot readiness, leases, queue drain, and public commands route remain
  unchanged after the rollout.

## Explicit non-goals

- No multi-primary Authentik database.
- No Grafana deployment on Oracle during this normal-path change.
- No Minecraft, Cartwise, or PantryBot deployment changes.
