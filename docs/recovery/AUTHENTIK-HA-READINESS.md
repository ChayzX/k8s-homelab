# Authentik emergency-access readiness

This bounded record covers homelab issues #198 and #202. It records sanitized
evidence only and does not authorize a second Authentik/PostgreSQL writer or
make Authentik a prerequisite for recovery.

## Current boundary

- Authentik server, worker, and LDAP outpost run with application capacity on
  both home and Oracle. Home is the normal interactive site; Oracle capacity
  is not an equal public origin while its local PostgreSQL is a standby. This
  is active-active application capacity, not multi-primary database operation.
- Authentik PostgreSQL has one writer per fencing epoch. The current live
  authority snapshot is home (`pg_is_in_recovery() = false`); Oracle is the
  streaming standby (`pg_is_in_recovery() = true`). Promotion and old-writer
  fencing are not yet proven end-to-end.
- Oracle and ChaseBot use SSSD over LDAPS and resolve `chase` and
  `posix-admins` (UID/GID 2018/27557).
- Local SSH/key recovery remains independent of Authentik.

Normal interactive route ownership is recorded in
`INTERACTIVE-ROUTE-OWNERSHIP.md`; Oracle receives those routes only after
controlled database promotion and local Authentik readiness validation.

## Live validation

The production Authentik provider, application, group binding, outpost
association, certificate attachment, and readiness endpoint were verified
read-only. The LDAPS certificate served to both hosts matches their installed
CA file. SSSD is enabled on both hosts with cached credentials and `pam_sss`
authentication/account/password/session modules.

| Gate | Oracle | ChaseBot |
| --- | --- | --- |
| Directory identity and group lookup | Pass | Pass |
| `%posix-admins` sudoers policy | Pass; effective `NOPASSWD:ALL` | Pass; effective `NOPASSWD:ALL` |
| Local key fallback | Pass with `ubuntu` and the Oracle key | Pass with `cpederson` and the mini-PC key |
| SSH/PAM configuration | PAM enabled; sshd password/keyboard-interactive disabled | PAM enabled; sshd password authentication permitted |
| Interactive directory authentication | Pass via a temporary synthetic user and local `su` PAM path | Pass via a temporary synthetic user and local `su` PAM path |
| LDAP-outage fallback | Pass: cached identity, password authentication, and `sudo -n` remained usable while the LDAP outpost was stopped | Pass: cached identity, password authentication, and `sudo -n` remained usable while the LDAP outpost was stopped |

The sudo rules are root-owned `/etc/sudoers.d/90-authentik-posix-admins` files;
`visudo -cf` passed on both hosts and effective policy was checked for `chase`.
No password or token values were recorded.

### Live PostgreSQL authority snapshot — 2026-09-19

Read-only queries against the live clusters reported:

- home `auth-postgresql-home-primary-0`: PostgreSQL 17.10,
  `pg_is_in_recovery() = false`;
- Oracle `auth-postgresql-standby-0`: PostgreSQL 17.10,
  `pg_is_in_recovery() = true`;
- home `pg_stat_replication`: application `auth-oracle-standby`, state
  `streaming`, asynchronous; write/flush/replay lag was approximately 0.13s at
  capture.

This is replication/freshness evidence only. It does not prove promotion,
fencing, session reconstruction, routing convergence, or failback.

An Authentik/PostgreSQL dump restore into an isolated PostgreSQL target reached
the Authentik readiness endpoint with HTTP 200. The isolated target used no
production PVC, Service, ingress, or scheduled writer and was deleted after
the rehearsal.

The restore now has a fail-closed, names-only contract check:

```bash
AUTHENTIK_RESTORE_NAMESPACE=<disposable-namespace> \
  scripts/authentik-restore-contract-check.sh
```

The checker refuses the production `auth` namespace, requires ready
PostgreSQL, Authentik server/worker, and LDAP outpost workloads, and verifies
the required Secret names and key names for database, signing, bootstrap,
LDAP bind, and outpost reconstruction without reading or printing values. A
passing result is only reconstruction preflight; it does not prove that the
restored LDAP provider, `posix-admins` policy, certificate, or web session
works.

The repository also contains `auth/home-postgresql-primary.yaml` as a
separately named, one-replica target for a controlled logical restore. It uses
a new `local-path` PVC and Secret references only; it has no replication
bootstrap, remote endpoint, NodePort, or normal Authentik route wiring. The
target is guarded by `tests/authentik-home-primary-contract-test.sh` and is not
an instruction to apply a second writer during normal operation. Any use still
requires the external fencing, promotion, readiness, and rollback gates below.

## Required gates before HA readiness

1. ~~Use a synthetic non-production directory credential to complete actual PAM
   authentication on both hosts; Oracle needs a deliberate local PAM test path
   because sshd does not expose password or keyboard-interactive auth.~~
   **Passed 2026-09-11:** a uniquely named temporary Authentik user was added
   to `posix-admins`, authenticated through the local `su` PAM path on Oracle
   and ChaseBot, and deleted after the test. SSH key access to both hosts was
   also confirmed while the LDAP outpost was unavailable.
2. In an isolated target, complete an Authentik web login and reconstruct the
   database, signing, provider, LDAP/outpost, bootstrap, and certificate
   Secret names by behavior without recording values. **Partial evidence
   2026-09-11:** the retained dump and copied configuration booted an isolated
   Authentik with the restored 12-user dataset; the flow accepted a synthetic
   username/password and reached WebAuthn registration. Full session completion
   and provider/LDAP/outpost/certificate behavior remain open.
3. ~~Make only the isolated LDAP target unavailable, repeat local-key logins,
   and document SSSD cache behavior and recovery.~~ **Passed 2026-09-11:**
   the LDAP outpost was scaled to zero and restored after the check; both hosts
   resolved the cached synthetic identity, accepted cached PAM authentication,
   and retained the cached `posix-admins` sudo policy. The outpost rollout was
   healthy after restoration.
4. Document and rehearse password rotation, account disablement, cache expiry,
   and rollback to local emergency accounts.
5. For issue #202, prove PostgreSQL promotion, measured RPO/RTO, old-writer
   fencing, session behavior, routing, and rollback without split-brain.

Until these gates have issue evidence, Authentik remains Oracle-primary for
database authority while both sites may serve application traffic. Returning
authority to home requires the documented fence/promote/endpoint/rollback
sequence; routing must not direct writes to a standby before that sequence has
completed its fencing, promotion, endpoint switching, and readiness checks.

## Safe verification commands

```bash
bash tests/authentik-manifest-test.sh
AUTHENTIK_RESTORE_NAMESPACE=<disposable-namespace> \
  scripts/authentik-restore-contract-check.sh
kubectl -n auth get deploy,sts,svc,pvc -o wide
kubectl -n auth get secret -o name
```

Secret values must never be printed or added to GitHub issues.
