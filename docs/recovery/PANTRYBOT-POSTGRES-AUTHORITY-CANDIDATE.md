# PantryBot PostgreSQL authority candidate

**Status:** design and disposable rehearsal only. No production database,
PantryBot deployment, DNS record, or automatic failover controller is changed
by this document.

## Decision

The candidate is **one PostgreSQL primary plus one asynchronous physical
streaming standby**, with Patroni-style ownership semantics and an external
fencing authority. Home is the normal primary site; Oracle is the warm standby
and promotion site. Both sites may run stateless PantryBot capacity, but only
the database holder for the current fencing epoch may accept writes.

This is active-active application capacity, not active-active database writes.
The 126–131 ms measured home-to-Oracle path is not suitable for synchronous
commit latency, and two independent writable PostgreSQL instances would make a
partition unsafe. Logical replication remains useful for experiments and
reporting, but it is not the authority mechanism because it does not provide a
standby that can be promoted with the same WAL history, does not replicate all
DDL/sequence/large-object behavior by default, and does not fence the source
writer.

The first implementation target is a manual, evidence-producing promotion
runbook. A future controller may automate it only after the exact fencing
adapter and endpoint route have passed the gate below.

## Why this candidate fits the available hosts

| Site | Role | Rehearsal budget | Constraint addressed |
|---|---|---:|---|
| Home (`chasebot`) | PostgreSQL primary | 500m CPU request / 1 CPU limit; 768Mi request / 1Gi limit; 8Gi PVC | Uses the second home node for stateful rehearsal without placing state on Minecraft. The budget leaves room for existing home workloads. |
| Oracle | PostgreSQL physical standby | 250m CPU request / 500m limit; 512Mi request / 768Mi limit; 8Gi PVC | Fits the 2-vCPU Oracle VM and leaves CPU for k3s/application capacity. No sustained media egress is required. |
| GCP observer/witness | Fencing/observation only | No PostgreSQL; at most 100m CPU / 256Mi if later enabled | The 969Mi VM is not a database host. Its account/quota and network cost gates remain unverified. |

The limits are rehearsal ceilings, not capacity claims. Before any production
use, record `kubectl top`, PVC growth, WAL retention, replication lag, and
Oracle egress. Stop if the host needs a resize, paid service, or sustained
cross-site media traffic.

## Authority and endpoint model

Each cluster gets a stable in-cluster Service named
`postgres-authority.pantry-bot.svc.cluster.local`. Its selector identifies the
current local primary (`pantrybot.postgres/role=primary`); the selector must be
changed only as part of the promotion transaction. The standby Service is
read-only and is never used as a write endpoint.

The application database URL is site-local and explicit:

```text
postgresql://...@postgres-authority.pantry-bot.svc.cluster.local:5432/pantry
```

Cross-site promotion has two endpoint steps: promote and label the Oracle
primary, then update the application site configuration to use Oracle's
`postgres-authority` Service before routing writes there. A DNS alias or
Cloudflare TCP route is not assumed; the current free plan has no paid global
database load balancer. The endpoint update is therefore a measured operator
or controller action, not an automatic claim.

## Replication comparison

| Option | Advantages | Disqualifying limits here | Decision |
|---|---|---|---|
| Current logical replication | Small, already transported home→Oracle, easy to inspect | One-way table-level behavior; schema/sequence/DDL gaps; source stays writable; no promotion/fencing semantics; lag can silently omit changes | Keep as a transport experiment only |
| Physical streaming + manual `pg_ctl promote` | Exact WAL history; standby promotion is PostgreSQL-native; low resource cost; clear LSN/RPO measurement | Async lag; manual endpoint change; still needs external old-writer fencing | **Candidate for the first safe rehearsal** |
| Patroni + Kubernetes DCS only | Health checks and lease-based primary selection; convenient in one cluster | Home and Oracle have independent k3s APIs; neither API is a neutral cross-site quorum; a partition can produce two local leaders; Kubernetes deletion is not STONITH | Not sufficient by itself |
| Patroni + external 3-member DCS plus fencing adapter | Gives a neutral election/epoch source and a reusable controller boundary | Needs a third independent witness, adapter credentials, and real fencing of an unreachable old writer; GCP eligibility and cost are not yet proven | Future automation candidate, not enabled |
| Synchronous cross-site PostgreSQL | Near-zero data loss | WAN latency, site outage availability, and Oracle/home resource limits make it unsuitable for the free plan | Reject |

## Old-writer fencing contract

The witness lease/epoch is an authorization signal, not physical fencing. A
promotion is unsafe unless the old PostgreSQL writer is made unable to commit.
The adapter must perform all of these actions, in order:

1. Acquire a monotonically increasing fencing epoch from a neutral witness.
2. Stop or isolate the old database writer using an independently reachable
   mechanism (for example, a provider/node power fence or a network ACL that
   blocks PostgreSQL traffic in both directions).
3. Prove the old endpoint cannot accept a test write and cannot reach the
   replication channel.
4. Promote the standby with `pg_ctl promote` and verify `pg_is_in_recovery()` is
   false and the expected epoch marker is committed.
5. Change the authority Service selector and application endpoint.
6. Reject any old-site lease or side-effect token whose epoch is lower than the
   new epoch.

`kubectl delete pod`, scaling a Deployment to zero, or waiting for a lease to
expire is not sufficient proof of database fencing. If the old site cannot be
reached by the adapter, automatic promotion must stop rather than guess.

## RPO/RTO targets and evidence

The design has no zero-RPO promise. For asynchronous replication:

```text
observed RPO ~= primary commit time - last replayed standby WAL time
```

The rehearsal must record primary `pg_current_wal_lsn()`, standby
`pg_last_wal_receive_lsn()`, standby `pg_last_wal_replay_lsn()`, and UTC times
at the failure boundary. The acceptance target for this candidate is **RPO <=
60 seconds in a steady-state rehearsal**, with the measured value recorded;
any larger value is a failed gate, not an averaged success.

The initial manual-promotion target is **RTO <= 15 minutes** from injected
primary loss to a successful synthetic write through the promoted endpoint.
Record detection, fence completion, promotion, endpoint update, application
readiness, and first successful write separately. These are targets for the
rehearsal, not evidence that production currently meets them.

## Executable non-production gate

The disposable manifests are:

- `pantrybot-postgres-authority-primary-home.yaml`
- `pantrybot-postgres-authority-standby-oracle.yaml`

The gate is `rehearse-pantrybot-postgres-authority.sh`. It requires two
explicit disposable kubeconfig contexts and refuses production-looking
contexts/namespaces. It performs:

1. apply primary and standby manifests;
2. wait for readiness and run `pg_basebackup` to initialize the standby;
3. create a tagged synthetic row on home;
4. poll receive/replay LSN and record the lag;
5. stop accepting traffic at the primary and run the configured **manual
   fencing command** supplied by the operator;
6. require an explicit `FENCE_PROOF_FILE` containing the old-writer rejection
   result;
7. promote Oracle, verify recovery state and the synthetic row;
8. write through the promoted endpoint and emit a JSON evidence record with
   RPO/RTO and cleanup instructions.

The script never invents a fencing result and never calls a production context.
An execution that omits the fencing proof is intentionally a failed rehearsal.
The manifests use placeholder passwords and disposable namespaces; no secret
from the live PantryBot or Authentik namespaces belongs in them.

## External gates that remain

These are the exact gates between this candidate and any production or
automatic failover claim:

1. **Home authority bootstrap:** create and independently escrow the
   `pantry-bot-platform` and `pantry-bot-witness` secrets, and provide a
   production PostgreSQL endpoint; neither current SQLite nor Authentik's
   PostgreSQL may be reused as-is.
2. **Physical replication rehearsal:** run the supplied gate with real
   disposable PVCs on home and Oracle, capture LSN/RPO/RTO evidence, and delete
   the namespaces afterward.
3. **Real fencing adapter:** choose and authorize a mechanism that can fence
   both home and Oracle even when the opposite site is partitioned; prove an
   old-writer commit is rejected. The current witness relay grants epochs but
   does not fence PostgreSQL.
4. **Neutral witness decision:** either keep promotion manual or provision a
   third, independently reachable witness. GCP eligibility, quota, egress, and
   memory impact are not yet recorded, so it cannot presently be treated as a
   free automatic-failover authority.
5. **Stable endpoint/routing:** implement and measure the application database
   endpoint update plus public/API route convergence; internal Kubernetes
   readiness is not enough.
6. **Full state migration:** import all required SQLite domain state and run
   parity/behavioral replay, including commands not yet PostgreSQL-backed.
7. **Application fence integration:** verify gateway, dispatcher, worker, and
   overlay tokens reject the old database epoch and duplicate side effects are
   idempotent after promotion.
8. **Capacity and cost evidence:** recheck CPU/memory/PVC/WAL growth, Oracle
   egress, GCP billing/quota, R2 retention, and measured RPO/RTO under load.
9. **Rollback and return-home rehearsal:** promote home back from Oracle with
   the same fence proof, then attach all evidence to GitHub issues #147 and
   #191. Until these gates pass, keep the legacy SQLite deployment recoverable
   and automatic failover disabled.

## Non-goals

This candidate does not modify production manifests, install Patroni, create a
paid load balancer, stretch k3s, reuse Authentik PostgreSQL, promise zero RPO,
or make Minecraft part of the HA database plan.
