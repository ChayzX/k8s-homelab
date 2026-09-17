# PantryBot PostgreSQL authority — disposable rehearsal evidence

Run: 2026-09-16T00:24Z UTC (local: 2026-09-15 19:24 CDT)
Gate: `docs/recovery/rehearse-pantrybot-postgres-authority.sh`
Candidate: physical PostgreSQL streaming (`postgres:16-alpine`)

## Result

PASSED for the physical-streaming candidate. Production was not changed
(`production_changed: false`). All workloads ran in disposable namespaces
(`pantry-bot-authority-rehearsal-home`, `pantry-bot-authority-rehearsal-oracle`)
and were removed on exit.

| metric | value |
|---|---|
| RTO (apply+promote) | 41 s |
| boundary primary LSN | `0/30001E0` |
| boundary replay LSN | `0/303BC68` |
| boundary WAL lag | ~0 (measurement-boundary noise) |
| fencing epoch | 1 (disposable) |
| promote | `su postgres -c 'pg_ctl -D /var/lib/postgresql/data promote'` |
| post-promote endpoint | verified, synthetic write OK |

## Sequence executed

1. Home primary StatefulSet (`postgres-authority`, node `minecraftmachine`,
   NodePort 30442) became ready.
2. Oracle standby init container ran `pg_basebackup -Fp -Xs -R` over the
   tailscale mesh from `100.84.89.87:30442` and started streaming.
3. Synthetic `rehearsal_events(..., 'rpo-rto-gate')` row inserted on home
   primary; Oracle standby replayed it (count=1).
4. Disposable home writer fenced: StatefulSet scaled to 0, pod force-deleted,
   connect to old writer refused.
5. Oracle standby promoted with `pg_ctl promote` (as `postgres` user); endpoint
   present; post-promotion insert OK.

## Gate fixes landed during rehearsal

- `rehearse-pantrybot-postgres-authority.sh`
  - primary manifest `nodeSelector` changed `chasebot` → `minecraftmachine`
    (chasebot is NotReady).
  - `kubectl wait` now `--for=create` then `--for=condition=ready` (modern
    kubectl fails fast "no matching resources found" otherwise).
  - `pg_ctl promote` runs via `su postgres -c '...'` (`pg_ctl: cannot be run as
    root` in `postgres:16-alpine`).
- Supporting kubeconfigs (`home`, `oracle`) were constructed outside the repo
  from `/etc/rancher/k3s/k3s.yaml` on each host; server rewritten to the node
  tailscale address. Not committed.

## Open divergence for live return-home (action needed)

Live inspection on Oracle shows the current PantryBot writer is the k3s
**container `canada-standby-prep-0`** (namespace `pantrybot-canada-replica-prep`,
hostNetwork `:25443`, `pg_is_in_recovery=false`). The
`postgres-authority-standby` StatefulSet the repo fence tooling targets is
scaled to **0/0 with no pods**, and its Service `postgres-authority-standby` is
already extinct/empty. The live `pantry-bot-platform` Secret points at
`postgres-authority-standby.pantry-bot.svc.cluster.local:5432` but the Serving
postgres listens on `100.78.181.15:25443`.

Therefore the as-designed return-home fences (`fence-pantry-postgres-oracle.sh`,
`fence-oracle-postgres-local.sh`) target an object that does not hold the live
writer. Before any live promotion, the fence tooling must be reconciled with the
actual live writer (`canada-standby-prep-0`), or the live return-home sequence
must go through the authorizing witness + real writer fence; otherwise a home
promotion would run without fencing the current writer (split-brain risk). This
is the same reconciliation issue #191 flags.