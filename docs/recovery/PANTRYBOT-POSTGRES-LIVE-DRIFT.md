# The live PantryBot primary has drifted from these manifests

Written 2026-09-25, after a cutover attempt exposed three defects that had all
been invisible while the system looked healthy. Read this before trusting
`pantrybot-postgres-authority-production.yaml` to describe what is running.

## The drift

`pantrybot-postgres-authority-production.yaml` defines a StatefulSet named
`postgres-authority`. **That StatefulSet is scaled to zero.** The live primary
is:

```
postgres-authority-standby-home-canada-0    pg_is_in_recovery() = f
```

It is the primary despite `standby` in its name. Home carries six Postgres
StatefulSets and Oracle seven; all but one on each side are at zero replicas.
Anyone promoting, backing up, or repointing by StatefulSet name will pick the
wrong one. Discover the primary by label instead:

```bash
kubectl -n pantry-bot get pods -l pantrybot.postgres/role=primary
```

## What this drift broke, and what is now fixed here

### 1. The replication NodePort had no endpoints

`Service/postgres-authority-replication` selected
`app.kubernetes.io/name: pantry-postgres-authority`. The live primary is
labelled `pantry-postgres-authority-standby-home-canada`. No match, no
endpoints, NodePort 30432 closed.

Replication still reported 0.13s lag, because it was running on a TCP
connection established while the names *did* match. **It could not have
re-established.** One pod restart and the standby would have been lost
silently.

Both Services in the production manifest now select on
`pantrybot.postgres/role: primary` alone. The role label moves with promotion,
so it needs no editing when the authority is renamed — and a Service that must
be edited on every rename is one that will be forgotten.

This has been applied to the live cluster.

### 2. pg_hba did not permit the replication source

A standby arriving through the NodePort is SNATed to the node's **LAN**
address, not its Tailscale one. `pg_basebackup` failed with:

```
FATAL: no pg_hba.conf entry for replication connection from host "192.168.40.208"
```

`192.168.40.208/32` is now in the ConfigMap here, alongside explicit Tailscale
entries for both nodes. **See the caveat below — this ConfigMap does not
currently reach the live primary.**

### 3. The replication role's password had drifted from the secret

`pantry-bot-postgres-replication` (Home) and `pantry-bot-postgres-standby`
(Oracle) hold the *same* value. The `pantry_replicator` role in the database
matched neither. Realigned with `ALTER ROLE ... PASSWORD` taken from the
secret.

Nothing in the repo would have revealed this. It is only observable by
attempting a replication connection, which is an argument for rehearsing
standby rebuilds rather than assuming replication lag means replication works.

## Caveat: the hba ConfigMap is not mounted

`postgres-authority-hba` is mounted by the `postgres-authority` StatefulSet —
the one scaled to zero. **The live primary does not mount it.** Its
`pg_hba.conf` exists only inside the PVC, where it was written by a
`pg_basebackup` from an earlier primary.

So the fix in (2) was applied directly to the running file and reloaded. It
survives a pod restart, because it lives in the volume. It does **not** survive
a rebuild of the primary from a fresh basebackup, and it is not reconciled from
this repo.

That is a genuine gap and it is not fixed here, because closing it means either
repointing the live StatefulSet at the ConfigMap (a restart of the production
primary) or rebuilding the authority onto the manifest in this file. Both are
real operations that deserve their own maintenance window rather than being
smuggled into a config commit.

Until then: **after any primary rebuild, re-check `pg_hba.conf` against the
ConfigMap in this directory before expecting a standby to attach.**

## What to verify after any promotion or primary rebuild

```bash
# 1. exactly one primary
kubectl -n pantry-bot get pods -l pantrybot.postgres/role=primary

# 2. the replication Service actually has endpoints
kubectl -n pantry-bot get endpoints postgres-authority-replication
#    expected: an address. <none> means no standby can ever attach.

# 3. the standby is streaming, not merely configured
kubectl -n pantry-bot exec <primary> -c postgres -- \
  psql -AtX -c "select application_name, client_addr, state, replay_lag
                from pg_stat_replication"

# 4. the slot is active
psql -AtX -c "select slot_name, active from pg_replication_slots"
```

Step 2 is the one that was silently false for an unknown length of time.
