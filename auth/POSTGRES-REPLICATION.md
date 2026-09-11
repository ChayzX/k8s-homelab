# Authentik PostgreSQL cross-site replication

The Authentik application tier is active-active, but its PostgreSQL state has
one writable authority per fencing epoch. The Oracle database is a physical,
read-only standby during normal operation. This is intentional: PostgreSQL
multi-primary writes are not provided by the free homelab design.

## Current topology

- Home primary: the Helm-managed `auth-postgresql-0` StatefulSet.
- Home transport: the private `auth-postgresql-transport` NodePort on `30433`.
- Oracle standby: `auth-postgresql-standby-0`, backed by a local-path 10Gi PVC.
- Replication slot: `auth_oracle_standby`.
- Replication mode: physical streaming replication, asynchronous.
- Oracle standby accepts read-only connections and must not receive Authentik
  application traffic while home owns the database epoch.

The standby manifest is [oracle-postgresql-standby.yaml](./oracle-postgresql-standby.yaml).
Its credentials and primary endpoint are supplied through the
`auth-postgresql-standby` Secret out of band; no credentials belong in Git.

## Source-of-truth PostgreSQL contract

[POSTGRES-REPLICATION-VALUES.yaml](./POSTGRES-REPLICATION-VALUES.yaml) carries
the secret-free Helm overlay for `wal_level`, senders, slots, WAL retention,
connection capacity, and replication HBA entries. The `10.42.0.0/16` rule is
needed because the Oracle pod reaches the home NodePort through pod-source NAT;
the host address rule alone is insufficient.

The replication role and slot are created once on the home primary. A Helm
upgrade must preserve those objects and must render the HBA entries from the
overlay before any primary restart. If a chart upgrade replaces the generated
ConfigMap, restore the HBA contract and verify streaming before declaring the
standby healthy.

## Promotion safety

Replication health is not promotion proof. Promotion requires all of the
following, in order:

1. An external witness grants a new database epoch.
2. The home PostgreSQL writer and every home Authentik writer are fenced or the
   home failure domain is independently confirmed unavailable.
3. The Oracle standby is promoted and `pg_is_in_recovery()` becomes false.
4. Authentik's database secret/endpoint and both site application deployments
   are pointed at the new authority.
5. Login, logout, LDAP bind, provider behavior, and application readiness pass.
6. Public/private routing is changed only after the new authority is ready.

Never promote Oracle while home can still commit. A stale home writer would
make the two databases diverge and invalidate the active-active safety model.
The guarded Authentik controller in
[`../observability/failover-witness/authentik_oracle_promoter.py`](../observability/failover-witness/authentik_oracle_promoter.py)
now packages the Oracle-side promotion sequence, including witness resource
`auth:postgres`, standby promotion, service selector switch, Authentik secret
endpoint update, and application restart. It is not enabled as a production
systemd unit: the remaining gate is a proven old-writer fencing mechanism plus
a non-production promotion rehearsal. Do not run it against production until
the fencing checklist below has been completed and independently observed.
The command requires `--old-writer-fence-command`; it executes that
out-of-band command after acquiring the new witness epoch and before promoting
Oracle, and aborts if the command fails. The command must fence the home
writer domain (for example, by remotely invoking the site-local
`fence-writer-domain.sh` helper), not merely acknowledge that fencing happened.
This remains an opt-in controller with no production service unit enabled.

## Verification

On home, query `pg_stat_replication` and `pg_replication_slots` as the
`authentik` role. Expected state is `streaming`, an active
`auth_oracle_standby` slot, and matching send/write/flush/replay LSNs.

On Oracle, expected state is `pg_is_in_recovery() = true`, a streaming WAL
receiver, and matching receive/replay LSNs. A healthy standby alone does not
authorize routing or promotion.
