# PantryBot production PostgreSQL authority bootstrap

**Status:** home authority bootstrap applied and verified on 2026-09-10. This
does not change the legacy PantryBot Deployment, move Minecraft, or claim HA
promotion.

This path creates one PostgreSQL authority in the existing `pantry-bot`
namespace. The pod is pinned to `chasebot`, uses the `local-path` StorageClass
with one 8Gi `ReadWriteOnce` PVC, and is reached only through the stable
ClusterIP Service:

```text
postgres-authority.pantry-bot.svc.cluster.local:5432
```

The resource budget follows the measured home cluster baseline: 500m CPU and
768Mi memory requests, with a 1 CPU and 1Gi memory limit. `chasebot` is inside
the home failure domain; this is not cross-site HA.

The authority starts with explicit physical-replication prerequisites:
`wal_level=replica`, 10 WAL senders, 10 replication slots, 256MiB retained WAL,
and a 1GiB per-slot WAL cap. These settings only make a future standby
possible; they do not expose PostgreSQL, create a replication role, or
authorize promotion.

The container runs the official PostgreSQL entrypoint as root only during
initial volume ownership setup, with the narrow `CHOWN`, `DAC_OVERRIDE`,
`FOWNER`, `SETGID`, and `SETUID` capabilities; the entrypoint drops to the
`postgres` user before starting the server. No privilege escalation is
allowed, and the database process is not granted a host namespace or host
mount.

## Required external secret escrow

Create `pantry-bot-postgres-authority` in namespace `pantry-bot` through the
approved external secret escrow process before bootstrap. The repository
contains no credential values. The Secret must contain these keys:

```text
POSTGRES_USER
POSTGRES_DB
POSTGRES_PASSWORD
```

The escrow process must also apply this metadata, with the reference matching
the operator's escrow record:

```yaml
metadata:
  labels:
    homelab/secret-escrow: external
  annotations:
    homelab/secret-escrow-reference: <ticket-or-vault-reference>
```

Do not reuse Authentik credentials, the legacy PantryBot SQLite state, or a
disposable rehearsal password. Do not commit a Secret manifest or paste
Secret values into this repository.

## Fail-closed preflight

Run from this directory with an explicitly selected production home context:

```bash
KUBE_CONTEXT=<production-home-context> \
EXTERNAL_SECRET_ESCROW_REF=<ticket-or-vault-reference> \
./bootstrap-pantrybot-postgres-authority.sh
```

The default mode is read-only and performs all of the following checks:

1. Requires an explicit context and escrow reference.
2. Refuses context names containing `rehearsal`, `disposable`, `oracle`, or
   `standby`.
3. Verifies the existing `pantry-bot` namespace and `local-path` StorageClass.
4. Verifies the externally escrowed Secret metadata and required key names
   without printing values.
5. Runs `kubectl apply --dry-run=client` against the production manifest.

No deployment is performed by the default command.

## Controlled apply

Applying is a separate, explicit operator action. After reviewing the
read-only output and confirming the escrow record, the only apply path is:

```bash
KUBE_CONTEXT=<production-home-context> \
EXTERNAL_SECRET_ESCROW_REF=<ticket-or-vault-reference> \
CONFIRM_PRODUCTION_AUTHORITY_BOOTSTRAP=yes \
./bootstrap-pantrybot-postgres-authority.sh --apply
```

After applying, verify the StatefulSet, pod readiness, PVC binding, and Service
endpoints. Do not point the legacy Deployment at this authority as part of
this bootstrap. Application migration, data parity, and cutover are separate
reviewed work.

The initial live verification passed:

- `postgres-authority-0` is Ready on `chasebot` using the pinned PostgreSQL
  digest;
- `data-postgres-authority-0` is Bound as an 8Gi local-path RWO PVC;
- `postgres-authority` has a Ready endpoint; and
- an in-pod `pg_isready` plus `SELECT current_database(), current_user,
  pg_is_in_recovery()` returned `pantry`, `pantry`, and `f`.

## Explicit separation from replication and fencing

This manifest deliberately has one replica and no replication client, standby,
promotion controller, fencing adapter, witness, or cross-site route.
The stable Service selector identifies the home primary only. A healthy pod or
Service is not proof that another site can be promoted safely.

Before any replication or promotion work, require separate evidence for:

- physical replication and measured RPO/RTO;
- an external fencing mechanism that rejects old-writer commits;
- a neutral witness/epoch decision, if automation is proposed;
- endpoint and application-fence behavior after promotion; and
- rollback and return-home rehearsal.

The disposable manifests and rehearsal script remain the reference for those
non-production experiments. They must not be changed into a production
promotion mechanism by editing this bootstrap path.

## Scope guard

This bootstrap does not modify:

- `pantry-bot/40-deployment.yaml` or any legacy PantryBot Deployment;
- Minecraft manifests, node selectors, PVCs, or scheduling;
- Oracle workloads or standby resources; or
- any issue tracker other than GitHub Issues.
