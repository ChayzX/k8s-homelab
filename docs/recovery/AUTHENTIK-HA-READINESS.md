# Authentik HA readiness

This is the bounded readiness record for homelab issue #198. It describes the
current identity-service boundary and the evidence required before controlled
promotion. It does not authorize a second writer, change the production
Authentik installation, or make Authentik a prerequisite for recovery access.

## Current boundary

- Authentik server and worker are home-primary on the `minecraftmachine` k3s
  control-plane host. Oracle application replicas remain scaled to zero while
  promotion and session/provider gates are open.
- PostgreSQL uses a local-path, single-writer PVC on home. Oracle now has a
  freshly reseeded 10Gi physical streaming standby through the private
  `auth-postgresql-transport` NodePort; the latest check showed both sides at
  `streaming` with matching LSNs. This is replication freshness evidence, not
  promotion or old-writer fencing proof.
- The tracked LDAP outpost is `auth/50-ldap-outpost.yaml`. It is intentionally
  one replica and uses only the `ldap-outpost-token` Secret; its pod does not
  need a Kubernetes service-account token.
- Oracle and ChaseBot have resolved the Authentik LDAP identity `chase` and the
  `posix-admins` group through SSSD. This proves NSS/group lookup only, not
  password authentication or an interactive SSH/PAM login.
- Emergency SSH/key access, external monitoring, and the independent Oracle
  standby must continue to work while Authentik and LDAP are unavailable.

## Evidence already recorded

- An Authentik/PostgreSQL dump was restored into an isolated PostgreSQL 17.10
  target; Authentik 2026.5.6 reached `/-/health/ready/` with HTTP 200.
- The isolated target used no production PVC, Service, ingress, or scheduled
  writer and was deleted after the rehearsal.
- The current backup and restore procedure is documented in
  `docs/recovery/RESTORE-REHEARSAL.md` and
  `scripts/authentik-postgres-backup.sh`.
- The Oracle standby was reseeded after an earlier rehearsal left it on a
  higher PostgreSQL timeline; the stale standby PVC was replaced from the
  current home writer. The source Service selector is tracked in
  `auth/60-postgresql-transport.yaml` so future changes cannot silently point
  replication at the retired ChaseBot standby.

These facts establish restore/readiness evidence, not HA or login acceptance.

## Required gates before calling it HA-ready

1. **Provider and secret reconstruction:** in an isolated target, verify the
   LDAP provider, `posix-admins` policy, outpost token, signing/provider
   secrets, bootstrap secret, and LDAPS certificate by name and behavior only;
   never record values.
2. **Interactive identity proof:** use a synthetic non-production account to
   complete an Authentik login and a host-side SSH/PAM login on Oracle and
   ChaseBot. Keep the existing `getent`/`id` checks as a separate NSS gate.
3. **Emergency fallback:** make Authentik/LDAP unavailable in the isolated
   target and prove direct host SSH plus the documented local recovery path
   still works. Record mechanism names and sanitized status only.
4. **Database promotion:** restore or replicate to a separately controlled
   PostgreSQL writer, measure freshness/RPO, fence the old writer, and prove
   that writes from the old primary are rejected before routing changes.
5. **Rollback:** record the reverse routing/fencing sequence and demonstrate
   that the original home writer can be resumed without split-brain.

Until gates 1–5 have evidence in issue #198, the supported model is
home-primary with isolated restore/controlled-promotion capacity. Do not scale
the production writer or deploy an Oracle Authentik/PostgreSQL writer as a
routine active-active pair.

## Safe verification commands

These commands inspect configuration or isolated targets only; they do not
read secret values or modify production:

```bash
bash tests/authentik-manifest-test.sh
kubectl -n auth get deploy,sts,svc,pvc -o wide
kubectl -n auth get secret -o name
```

For the restore and login gates, use the isolated procedure in
`docs/recovery/RESTORE-REHEARSAL.md`, then attach sanitized output and the
target boundary to issue #198.
