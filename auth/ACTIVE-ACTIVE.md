# Authentik active-active application tier

Authentik is part of the non-Minecraft active-active target. The server,
worker, and LDAP outpost must run capacity in both home and Oracle. The sites use
the same Authentik secret key, provider configuration, certificates, and
session settings.

The application tier is active-active; PostgreSQL remains one writable
authority per fencing epoch. This is required to prevent duplicate identity
state writes and split-brain session/provider changes. During normal
operation, both sites may serve requests through the routing layer. During a
database-site transition, the old writer is fenced, the standby is promoted,
and both sites are pointed at the new authority before the old site can accept
writes again.

## Helm capacity contract

The existing Helm release is installed from a host-local values file. The
following settings are the tracked contract for both site values files; the
database host and secret names remain site-specific and must never contain
secret values in git:

```yaml
authentik:
  existingSecret:
    secretName: auth-authentik
  postgresql:
    enabled: false

server:
  replicas: 2
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              app.kubernetes.io/component: server
          topologyKey: kubernetes.io/hostname

worker:
  replicas: 2
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              app.kubernetes.io/component: worker
          topologyKey: kubernetes.io/hostname
```

The LDAP outpost follows the same two-replica, hostname anti-affinity
contract. Its token is an existing Secret and is not copied into the values
file. Oracle's values file must point at the current database authority
transport; it must not create a second writable PostgreSQL database during
normal operation.

## Promotion gates

Before Oracle Authentik capacity is enabled for public traffic, evidence must
show:

1. Secret-key, provider, certificate, and session configuration parity.
2. Both sites' server, worker, and LDAP readiness with the same database
   authority.
3. PostgreSQL fencing and promotion with no old writer able to commit.
4. Login, logout, WebAuthn progression, LDAP bind, and provider behavior from
   each site.
5. Rollback to the prior database authority without stale-session or provider
   corruption.

The isolated restore contract verifies the recovery components but does not
by itself satisfy these active-active gates.

PostgreSQL replication is now packaged separately in
[`POSTGRES-REPLICATION.md`](./POSTGRES-REPLICATION.md). Oracle has a live
physical standby and the home primary currently reports streaming with an
active replication slot. This proves standby freshness, not automatic
promotion: external epoch authority, old-writer fencing, promotion, and
post-promotion routing remain required.

## Current deployment boundary

The repository contract targets two replicas with hostname anti-affinity for
the application and LDAP tiers. The live cross-site deployment provides
Authentik server, worker, and LDAP capacity in both home and Oracle, using the
same configuration and the home database authority. Same-site replica density
is a separate capacity concern: home currently runs one server, two workers,
and one LDAP outpost; Oracle runs one server, two workers, and two LDAP
outposts. A scale attempt on 2026-09-11 caused ChaseBot's node to report
ephemeral-storage pressure and evict the extra server/LDAP pods, so those
replicas were rolled back after the remaining workloads returned Ready.

Do not describe same-site two-replica density as complete until ChaseBot's
storage pressure is resolved and the anti-affinity rollout is re-proven. The
cross-site active-active application capacity and database/fencing gates must
still be verified independently.
