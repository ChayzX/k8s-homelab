# PantryBot Oracle cutover runbook

Tracking: k8s-homelab#314. Companion documents:
`docs/recovery/PANTRYBOT-POSTGRES-BACKUP-PITR-DESIGN.md` (backup design, gates
step 1.2 of this runbook) and `docs/recovery/PANTRYBOT-COMMANDS-TUNNEL-PLAN.md`
(the Cloudflare API conventions reused in step 5).

## What this is

**This is a promote, not a migration.** Oracle already runs a streaming replica
of the live primary: same `system_identifier`, sub-second replay lag, an active
replication slot on the primary. No data is copied during this procedure. The
database work is one `pg_promote()` call.

Everything else is sequencing: stop writing at Home, let the replica reach
byte-equality, promote, repoint `PANTRY_DATABASE_URL`, start Oracle's app tier,
and move the Cloudflare connectors — which is the only part that is genuinely
delicate, because Home and Oracle share one tunnel token per tunnel and
scaling Oracle up without scaling Home down produces a silent 50/50 split
rather than a cutover.

## Decisions this runbook assumes (settled; not re-opened here)

- Oracle becomes the single serving site. Oracle stays Always Free.
- Home becomes a **cold** restore target: a current backup plus manifests.
  Nothing running, no witness, no promoter, no fencer, no automatic failover.
- Authentik and Grafana **stay on Home**. Cloudflare tunnel `59569621`
  (oauth / auth / grafana / operations / cartwise) is **never touched by this
  runbook**. If you find yourself editing it, stop.
- Everything stays inside free tiers.
- RTO target is 30 minutes for "Oracle alive, database lost". The
  "Oracle gone, restore at Home" case is not a 30-minute path today — see the
  backup design document.

## Names that are actively misleading — read this before touching anything

| What you see | What it actually is |
|---|---|
| StatefulSet `postgres-authority-standby-home-canada-0` | **The live production primary.** `pg_is_in_recovery()` returns `false`. Every app's `PANTRY_DATABASE_URL` points here. |
| StatefulSet `postgres-authority` | Scaled to 0. Not running. Historical. |
| StatefulSet `postgres-authority-home` | Scaled to 0. Not running. Historical. |
| `observability/failover-witness/home/postgres-authority-standby-home-canada.yaml` | The committed manifest for the live primary, pinned `replicas: 0`. **Never `kubectl apply -f` it.** Patch or `kubectl scale` instead. |
| Oracle standby StatefulSet | **Not in the repository at all.** Only its PVC (`data-postgres-authority-standby-oracle-v2-0`) is referenced, by a one-shot reseed Pod. Verify the live object name in step 1.0 rather than trusting this document. |
| "Routing is handled by `publish-cloudflare-routes.sh`" | A no-op between Home and Oracle — both sites' entries in `cloudflare-route-inputs.json` are identical. That tooling exists for Canada. Connector cutover here is done by hand, in step 5. |

There is **not one Kubernetes Ingress** in this estate. All public routing is
Cloudflare token-mode tunnels; the ingress rules live at Cloudflare.

## Preconditions and operator setup

Run everything from one shell with both kubeconfigs in hand. Never let a
command default to whichever context happens to be current.

```bash
export HOMECFG=/path/to/home.kubeconfig
export ORACFG=/path/to/oracle.kubeconfig
export NS=pantry-bot

# Verify each kubeconfig points where you think it does, before anything else.
kubectl --kubeconfig="$HOMECFG" get nodes -o name
kubectl --kubeconfig="$ORACFG"  get nodes -o name
```

Expected: Home lists `node/minecraftmachine` (plus any others); Oracle lists
`node/pantry-bot-oracle`. If either is wrong, stop.

Cloudflare, for step 5 (token scoped to tunnel read/edit plus the
`greeniespantry.uk` zone — never a Global API Key):

```bash
export CF_ACCOUNT_ID='<operator-supplied account id>'
export APP_TUNNEL_ID='30dde1eb-e625-4dd4-b5ac-2d385a1b7336'       # PantryBot-App
export COMMANDS_TUNNEL_ID='c0015a8b-3f9e-4af9-b172-a97b882b4b28'  # PantryBot-Commands
# Read the token from a 0600 file; do not put it in argv or shell history.
export CF_TOKEN_FILE="$HOME/.config/pantry/cf-token"
cf() { curl --fail-with-body -sS -H "Authorization: Bearer $(cat "$CF_TOKEN_FILE")" \
         -H 'Content-Type: application/json' "$@"; }
```

Known origin IPs, used to tell the two sites apart in connector listings:

- Home: `98.159.20.40`, edge colos `ord*`
- Oracle: `92.5.26.74`, edge colos `fra*`

## Scheduling

**This must not be run mid-stream.** The gateway Deployment uses
`strategy: Recreate` with `replicas: 1`, so chat reading stops the moment
Home's gateway pod is deleted and does not resume until Oracle's gateway pod
has started, connected, and acquired the PostgreSQL lease
`pantry:twitch:ingress`. That is the dark window, and in this procedure it is
deliberately widened to cover the promote.

**Expected chat-dark window: 9–14 minutes** (step 2.1 through step 4.3).
Commands and overlay are dark for a shorter window; `commands.greeniespantry.uk`
is dark for **zero** minutes because that tunnel is already split across both
sites and Oracle keeps serving it throughout.

Pick a slot with no stream scheduled for at least 90 minutes. Announce it.

## Timing summary

| Step | What | Wall clock | Reversible? |
|---|---|---|---|
| 1 | Pre-flight | 30–40 min (or +2 h if the backup gate fires) | n/a, read-only |
| 2 | Quiesce writes, drain, reach zero lag | 6–9 min | Yes — scale Home back up |
| 3.1 | Final LSN equality check | 1 min | Yes — last exit |
| **3.2** | **Promote Oracle — POINT OF NO RETURN** | 30–60 s | **No** |
| 3.3–3.7 | Shut down Home's primary, repoint secret, leases, ANALYZE | 4–6 min | No |
| 4 | Start Oracle's app tier | 4–6 min | No |
| 5a | Commands tunnel → single-homed on Oracle | 3–4 min | Yes (routing only) |
| 5b | App tunnel → Oracle | 4–6 min | Yes (routing only) |
| 6 | CI/CD runner labels | 15 min, may be deferred | Yes |
| 7 | Verification | 20–25 min | n/a |
| | **Total to serving** | **≈ 55–75 min** | |

---

# 1. Pre-flight (read-only, 30–40 min)

Nothing in this section changes anything. If any check fails, stop and fix it
before the window.

## 1.0 Identify the live objects (do not trust names)

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" get statefulset -o wide
kubectl --kubeconfig="$ORACFG"  -n "$NS" get statefulset -o wide
```

Record the Home primary pod (expected `postgres-authority-standby-home-canada-0`)
and the Oracle standby pod (expected `postgres-authority-standby-oracle-v2-0`,
**unverified — it has no committed manifest**). Export them:

```bash
export HOMEPOD=postgres-authority-standby-home-canada-0
export ORAPOD=postgres-authority-standby-oracle-v2-0
hpsql() { kubectl --kubeconfig="$HOMECFG" -n "$NS" exec "$HOMEPOD" -- \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -c "'"$1"'"'; }
opsql() { kubectl --kubeconfig="$ORACFG" -n "$NS" exec "$ORAPOD" -- \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -c "'"$1"'"'; }
```

Confirm which one is the primary — by behaviour, not by name:

```bash
hpsql "select pg_is_in_recovery()"    # expect: f   <-- this is the PRIMARY
opsql "select pg_is_in_recovery()"    # expect: t   <-- this is the REPLICA
```

**If Home returns `t` or Oracle returns `f`, stop immediately.** Something has
already promoted, and this runbook's assumptions are void.

## 1.1 Verify replication lag and shared lineage

Same lineage — the two values must be byte-identical:

```bash
hpsql "select system_identifier from pg_control_system()"
opsql "select system_identifier from pg_control_system()"
```

If they differ, Oracle is **not** a replica of this primary and this is a data
move, not a promote. Stop; this runbook does not apply.

Replay lag on Oracle:

```bash
opsql "select now() - pg_last_xact_replay_timestamp() as replay_lag,
       pg_last_wal_receive_lsn() as received, pg_last_wal_replay_lsn() as replayed"
```

Expected: `replay_lag` under 2 seconds (measured ~0.79 s), `received` and
`replayed` equal or within a few kB. If lag is minutes, do not proceed —
find out why before the window, not during it.

Slot and sender health on Home:

```bash
hpsql "select slot_name, slot_type, active, wal_status,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) as retained
       from pg_replication_slots"
hpsql "select client_addr, state, sync_state, sent_lsn, write_lsn, flush_lsn, replay_lsn
       from pg_stat_replication"
```

Expected: slot `pantry_oracle_standby` with `active = t` and
`wal_status = reserved`; one `pg_stat_replication` row with
`state = streaming`, `sync_state = async`, and the four LSNs close together.
`wal_status` of `extended` or `lost` is a hard stop.

## 1.2 Backup gate — **THIS STEP BLOCKS**

**As established, there is no PostgreSQL backup of PantryBot anywhere on
either cluster.** The only backup credential in the namespace,
`pantry-bot-litestream`, backs up a SQLite file belonging to a Deployment that
is scaled to zero. Continuous backup is a stated requirement and has never run.

A promote is not a backup. If the promote goes wrong — a corrupt replay, a
timeline you cannot rewind, a schema surprise — with no dump you have nothing
to go back to, because step 3.3 stops the old primary.

**Do not execute step 3.2 until a verified dump exists off-cluster.** The
minimum acceptable artifact, modelled on the working, rehearsed
`scripts/authentik-postgres-backup.sh`:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/mnt/nvme/recovery/pantrybot/pantrybot-$STAMP.dump.gz"
mkdir -p "$(dirname "$OUT")"

kubectl --kubeconfig="$HOMECFG" -n "$NS" exec "$HOMEPOD" -- \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  | gzip -9 > "$OUT"

# Integrity, not just exit status.
gzip -t "$OUT"
test "$(stat -c%s "$OUT")" -gt 1000000 || { echo "dump implausibly small"; exit 1; }
gzip -dc "$OUT" | pg_restore --list | head -20   # must list real objects
gzip -dc "$OUT" | pg_restore --list | wc -l      # record this number
```

Then copy it off the machine that holds the database, and verify the remote
copy by hash **and** exact byte count — the existing
`scripts/authentik-postgres-backup.sh` does both; copy that pattern rather than
trusting `rclone copy`'s exit code:

```bash
rclone copyto "$OUT" "r2:pantry-bot-backups/recovery/pantrybot/$(basename "$OUT")" \
  --s3-no-check-bucket
rclone check "$OUT" "r2:pantry-bot-backups/recovery/pantrybot/$(basename "$OUT")" \
  --s3-no-check-bucket --one-way
rclone size "r2:pantry-bot-backups/recovery/pantrybot/" --s3-no-check-bucket
```

Two things that will bite here, both from the backup design document:

- **The R2 free tier may already be exhausted.** No uploader in this fleet
  implements remote retention, and the bucket takes roughly 650 MB/day against
  a 10 GB free allowance. Run `rclone size r2:pantry-bot-backups
  --s3-no-check-bucket` **before** the window. If it is near 10 GB, add a
  bucket lifecycle rule (free) first, or stage the dump somewhere else. "It
  must be free" is a hard requirement and an overage is a failure.
- **`pg_restore` is what proves a dump, not `pg_dump`.** Restoring into a
  disposable namespace was rehearsed for Authentik on 2026-09-19; do the same
  here if the window allows it. If not, at minimum keep the `pg_restore --list`
  output.

**If you cannot produce and verify this dump, the cutover does not proceed.**
Everything after step 3.2 is irreversible; losing the database is the one
failure mode with no recovery path.

## 1.3 Capture rollback state

```bash
mkdir -p ~/cutover-$STAMP && cd ~/cutover-$STAMP && chmod 700 .

# Replica counts on both clusters, so rollback is mechanical.
kubectl --kubeconfig="$HOMECFG" -n "$NS" get deploy \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas > home-replicas.txt
kubectl --kubeconfig="$ORACFG" -n "$NS" get deploy \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas > oracle-replicas.txt

# Tunnel configurations, before any change.
cf "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$APP_TUNNEL_ID/configurations" \
   -o app-tunnel-before.json
cf "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$COMMANDS_TUNNEL_ID/configurations" \
   -o commands-tunnel-before.json
chmod 600 ./*.json
```

Also record, **out of band and not into any file in this repository**, the
current value of `PANTRY_DATABASE_URL` in the `pantry-bot-platform` Secret on
both clusters. You need the Home value to roll back and the Oracle value to
know whether it already points at Oracle's local postgres or still at Home's
Tailscale address.

```bash
# Print the host only, never the whole URL with its password.
kubectl --kubeconfig="$ORACFG" -n "$NS" get secret pantry-bot-platform \
  -o jsonpath='{.data.PANTRY_DATABASE_URL}' | base64 -d | sed 's|.*@|host: |; s|/.*||'
```

## 1.4 Confirm Oracle is actually ready to serve

```bash
# The overlay renders cleanly and the images it names exist on the node.
kubectl --kubeconfig="$ORACFG" -n "$NS" get deploy -o wide
kubectl --kubeconfig="$ORACFG" -n "$NS" get secret pantry-bot-platform \
  -o jsonpath='{.data}' | tr ',' '\n' | cut -d'"' -f2   # key names only
kubectl --kubeconfig="$ORACFG" -n "$NS" get secret \
  app-cloudflared-tunnel-token commands-cloudflared-tunnel-token
```

Expected: every PantryBot Deployment present on Oracle (at whatever replica
count it currently carries), the `pantry-bot-platform` Secret carrying at least
`PANTRY_DATABASE_URL`, `PANTRY_SITE_ID`, and the Twitch/YouTube credentials,
and both tunnel-token Secrets present. A missing Secret discovered at step 4 is
a self-inflicted outage.

`PANTRY_SITE_ID` on Oracle must be `oracle`. It is part of the lease `ownerId`
(`${PANTRY_SITE_ID}-${PANTRY_INSTANCE_ID}`); if both sites report the same site
id, lease arbitration between them degrades.

## 1.5 Known gaps to accept explicitly before starting

- **The maintenance responder does not exist on Oracle.**
  `pantry-maintenance-responder` is defined in `k8s/ha/home/responder.yaml`,
  deliberately outside `../base` so no other site's overlay can create a second
  one. After this cutover the bot loses its "we are down" voice — which is
  exactly the capability you want during a consolidation. Either move that
  resource into the Oracle overlay before the window, or accept the loss and
  record it.
- **CI depends on Home** (step 6). Not a blocker for the cutover itself.
- **No Prometheus alert rules and no Alertmanager exist in this estate.** A
  failing CronJob after cutover produces a failed Job object and silence.

---

# 2. Quiesce writes and let the replica catch up (6–9 min)

**Reversible in full. Nothing here is destructive.** If you abort during this
section, scale Home back up from `home-replicas.txt` and the system resumes.

## 2.1 Stop ingestion first (chat-dark window starts here)

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" scale deploy/pantry-twitch-gateway --replicas=0
kubectl --kubeconfig="$HOMECFG" -n "$NS" rollout status deploy/pantry-twitch-gateway --timeout=90s
```

The gateway holds the PostgreSQL lease `pantry:twitch:ingress` and releases it
on clean shutdown. `terminationGracePeriodSeconds` is not set, so the default
30 s applies; if the process is SIGKILLed the lease row is simply left to
expire after `PANTRY_LEASE_MS` (30 s). Either way, nothing else may attach a
Twitch socket while that lease is held.

Expected: `0/0` replicas within ~35 s. **Chat reading has now stopped.**

## 2.2 Drain the outbox, then stop the senders

Give the dispatchers and workers 60 seconds to finish what is already queued
before stopping them. The outbox is at-least-once, so a hard stop is safe, but
a drain avoids a burst of retries on the other side.

```bash
sleep 60
hpsql "select status, count(*) from pantry_outbox group by 1 order by 1"
```

Expected: `pending` at or near 0. Then:

```bash
for d in pantry-twitch-dispatcher pantry-chat-worker pantry-overlay-delivery \
         pantry-private-api pantry-private-site pantry-commands-site \
         pantry-maintenance-responder; do
  kubectl --kubeconfig="$HOMECFG" -n "$NS" scale "deploy/$d" --replicas=0
done
kubectl --kubeconfig="$HOMECFG" -n "$NS" get deploy
```

Expected: every PantryBot workload at `0/0`. Leave both cloudflared
Deployments running for now — step 5 handles those, and routing to a dead
origin is a clean 502 rather than a split.

## 2.3 Confirm no client is still writing

```bash
hpsql "select count(*) from pg_stat_activity
       where backend_type = 'client backend' and state <> 'idle'"
```

Expected: `0` (or 1, your own psql session). If a stray writer remains, find
it before continuing:

```bash
hpsql "select pid, usename, client_addr, state, left(query, 60)
       from pg_stat_activity where backend_type = 'client backend'"
```

## 2.4 Freeze the primary read-only

Belt and braces against anything you missed, and it makes step 3.1 meaningful.

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" exec "$HOMEPOD" -- sh -c \
 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER SYSTEM SET default_transaction_read_only = on" \
  && psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_reload_conf()"'
hpsql "show default_transaction_read_only"     # expect: on
```

**Rollback for 2.1–2.4:** set `default_transaction_read_only = off`, reload,
and restore replica counts from `home-replicas.txt`. Total rollback time ~4
minutes. Nothing has been lost.

## 2.5 Wait for byte-equality

```bash
hpsql "select pg_current_wal_lsn()"
opsql "select pg_last_wal_replay_lsn()"
```

Repeat until the two strings are identical. With writes stopped this happens in
about one second. Poll rather than guess:

```bash
for i in $(seq 1 30); do
  H=$(hpsql "select pg_current_wal_lsn()"); O=$(opsql "select pg_last_wal_replay_lsn()")
  echo "$i home=$H oracle=$O"; [ "$H" = "$O" ] && break; sleep 2
done
```

Expected: equality within a couple of iterations. If it never converges,
**abort and roll back** — the replica is not keeping up and promoting it
would lose transactions.

---

# 3. Promote Oracle

## 3.1 Final check — this is the last exit

```bash
hpsql "select pg_is_in_recovery(), pg_current_wal_lsn()"      # f, <LSN>
opsql "select pg_is_in_recovery(), pg_last_wal_replay_lsn()"  # t, <same LSN>
hpsql "select system_identifier from pg_control_system()"
opsql "select system_identifier from pg_control_system()"     # identical
ls -l ~/cutover-$STAMP/../pantrybot-*.dump.gz 2>/dev/null || \
  ls -l /mnt/nvme/recovery/pantrybot/                          # the dump exists
```

All four must hold: Home is the primary, Oracle is the replica, the LSNs match
exactly, the system identifiers match, and the verified dump from step 1.2
exists off-cluster. **If any one of them does not hold, roll back now.**

---

> # ⛔ POINT OF NO RETURN — everything below this line is irreversible
>
> The moment `pg_promote()` returns, Oracle starts a new timeline. Home's
> database is no longer a valid primary for this data and cannot be made one
> again without a reseed from Oracle. Every step after this point is forward-only.
>
> After this line, "rollback" means "restore from the step 1.2 dump", not
> "scale Home back up". Scaling Home back up after Oracle has taken writes
> gives you two divergent databases, which is worse than an outage.
>
> A connector rollback (step 5) is still mechanically possible, but it is **not**
> a rollback of the cutover: routing traffic back to Home reaches an app tier
> whose database is gone. Do not confuse the two.

---

## 3.2 Promote (30–60 s)

```bash
opsql "select pg_promote(wait => true, wait_seconds => 60)"
```

Expected: `t`.

```bash
opsql "select pg_is_in_recovery()"                 # expect: f
opsql "select pg_current_wal_lsn()"                # expect: advancing
opsql "select timeline_id from pg_control_checkpoint()"  # expect: previous + 1
```

If `pg_promote` returns `f` or times out, check the pod logs before touching
anything else:

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" logs "$ORAPOD" --tail=100
```

A failed promote leaves Oracle in recovery and Home frozen-but-intact — the
one case where rolling back to Home is still correct. Un-freeze Home (2.4
reversed), restore replica counts, and investigate out of band.

## 3.3 Stop Home's database (prevents split brain)

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" scale \
  statefulset/postgres-authority-standby-home-canada --replicas=0
kubectl --kubeconfig="$HOMECFG" -n "$NS" get pods -l app -o wide | grep postgres || true
```

Expected: the pod terminates within ~30 s.

**Use `kubectl scale`, not `kubectl apply`.** The committed manifest for this
StatefulSet carries `replicas: 0` *and* other fields you do not want re-applied
to a live object. The PVC retention policy is `Retain`/`Retain`, so the data
directory survives; keep it until the new backups have proven themselves.

## 3.4 Repoint `PANTRY_DATABASE_URL` (2 min)

Every serving component on Oracle reads `PANTRY_DATABASE_URL` from the Secret
`pantry-bot-platform`: `k8s/ha/base/gateway.yaml`, `dispatcher.yaml`,
`worker.yaml`, `overlay.yaml`, `api.yaml`, and `postgres-analyze-cronjob.yaml`.
There is exactly one place to change.

Build the new URL in a file, not on the command line (it contains a password):

```bash
umask 077
# Point at Oracle's postgres over the cluster-local address it actually listens on.
# Confirm the host with:
kubectl --kubeconfig="$ORACFG" -n "$NS" get svc,statefulset -o wide
printf 'postgresql://<user>:<password>@<oracle-host>:5432/<db>?sslmode=disable' \
  > /tmp/pantry-dburl
kubectl --kubeconfig="$ORACFG" -n "$NS" patch secret pantry-bot-platform \
  --type merge \
  -p "{\"stringData\":{\"PANTRY_DATABASE_URL\":\"$(cat /tmp/pantry-dburl)\"}}"
shred -u /tmp/pantry-dburl
```

Verify the host only, never the whole value:

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" get secret pantry-bot-platform \
  -o jsonpath='{.data.PANTRY_DATABASE_URL}' | base64 -d | sed 's|.*@|host: |; s|/.*||'
```

**Note on `hostNetwork`:** the old primary ran with `hostNetwork: true` and
Home's Tailscale IP hard-coded into its postgres args
(`listen_addresses=127.0.0.1,100.84.89.87`). Oracle's standby is configured the
same way for its own address. Whatever host you put in the URL, prove it with a
connection from inside the cluster before scaling anything up:

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" run dbcheck --rm -it --restart=Never \
  --image=postgres:16-alpine --env="U=$(kubectl --kubeconfig="$ORACFG" -n "$NS" \
  get secret pantry-bot-platform -o jsonpath='{.data.PANTRY_DATABASE_URL}' | base64 -d)" \
  -- sh -c 'psql "$U" -AtX -c "select pg_is_in_recovery(), current_database()"'
```

Expected: `f|<dbname>`. Anything else and you must not proceed to step 4.

## 3.5 Clear stale lease rows

`pantry_leases` replicated from Home, so Oracle has inherited Home's last owner
ids and epochs. Normally those rows expire within `PANTRY_LEASE_MS` (30 s) and
Oracle's pods acquire at `epoch + 1`. Check rather than assume — a replicated
row with a far-future `lease_until` would block Oracle's acquire, and you would
see it as "the gateway starts but never connects":

```bash
opsql "select resource, owner_id, epoch, lease_until, lease_until > now() as still_valid
       from pantry_leases order by resource"
```

Expected: every row either absent or with `still_valid = f` and an `owner_id`
beginning `home-`. If any row is still valid and owned by `home-`, and the Home
pods are confirmed gone (step 2.2 and 3.3), release it:

```bash
opsql "delete from pantry_leases where owner_id like 'home-%'"
```

Only do this with Home's workloads verifiably at zero. These leases are the
single-writer interlock; deleting a row whose owner is alive is exactly the
failure they exist to prevent.

## 3.6 `ANALYZE`

A promote preserves planner statistics, unlike a restore — but it costs seconds
and removes a whole class of "it came up working but unusably slow" confusion.
For reference, missing statistics cost Authentik 1.4 s per login step against
13 ms with them.

```bash
opsql "analyze"
opsql "select count(*) from pg_stats where schemaname = 'public'"   # expect: non-zero, large
```

## 3.7 Confirm the new primary accepts writes

```bash
opsql "create table if not exists _cutover_probe(t timestamptz)"
opsql "insert into _cutover_probe values (now()) returning t"
opsql "drop table _cutover_probe"
```

Expected: an inserted timestamp. If this fails with a read-only error, check
that `default_transaction_read_only` was never set on Oracle.

---

# 4. Start Oracle's app tier (4–6 min)

Start consumers before producers, so the queue is being drained by the time
chat events arrive.

```bash
# 4.1 Data plane and readers first.
for d in pantry-private-api pantry-chat-worker pantry-overlay-delivery; do
  kubectl --kubeconfig="$ORACFG" -n "$NS" scale "deploy/$d" --replicas=2
done
for d in pantry-private-site pantry-commands-site; do
  kubectl --kubeconfig="$ORACFG" -n "$NS" scale "deploy/$d" --replicas=2
done
kubectl --kubeconfig="$ORACFG" -n "$NS" rollout status deploy/pantry-private-api --timeout=180s
```

```bash
# 4.2 Senders.
kubectl --kubeconfig="$ORACFG" -n "$NS" scale deploy/pantry-twitch-dispatcher --replicas=1
kubectl --kubeconfig="$ORACFG" -n "$NS" rollout status deploy/pantry-twitch-dispatcher --timeout=180s
```

```bash
# 4.3 Gateway last. Chat reading resumes when this pod acquires the lease.
kubectl --kubeconfig="$ORACFG" -n "$NS" scale deploy/pantry-twitch-gateway --replicas=1
kubectl --kubeconfig="$ORACFG" -n "$NS" rollout status deploy/pantry-twitch-gateway --timeout=180s
kubectl --kubeconfig="$ORACFG" -n "$NS" logs deploy/pantry-twitch-gateway --tail=50
```

Expected in the gateway log: a lease acquisition for `pantry:twitch:ingress`
with an `owner_id` starting `oracle-`, then an IRC connect and channel join.
**Chat-dark window ends here.**

```bash
opsql "select resource, owner_id, epoch from pantry_leases order by resource"
```

Expected: `pantry:twitch:ingress`, `pantry:twitch:chat-outbound`,
`pantry:twitch:channel-points-outbound`, `pantry:twitch:moderation-outbound`,
`pantry:twitch:whisper-outbound`, `pantry:overlay`,
`pantry:youtube:chat-outbound`, `pantry:youtube:moderation-outbound` — eight
resources, every `owner_id` beginning `oracle-`.

If any `owner_id` still begins `home-` and is renewing, **stop and find the
Home pod that is still alive.** That is a live split-writer.

---

# 5. Connector cutover

This is the part that goes wrong if you improvise.

**Home and Oracle share one tunnel token per tunnel.** A connector that starts
at Oracle while Home's is still running does not take over — Cloudflare
load-balances across all registered connectors, so you get a silent 50/50
split, half of it pointing at a site whose database is now gone. The rule is
absolute:

> **Scale the departing site's connector to 0, confirm its connectors are gone
> from the Cloudflare API, and only then scale the arriving site's connector to 1.
> They must never overlap.**

The listing call, used before and after every scale:

```bash
connectors() {  # $1 = tunnel id
  cf "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$1/connections" \
  | jq -r '.result[] | [.id[0:8], .origin_ip, .colo_name, .opened_at] | @tsv'
}
```

## 5a. Commands tunnel first (`c0015a8b`) — zero downtime, 3–4 min

Do this one first. It is already split across both sites, so Oracle keeps
serving `commands.greeniespantry.uk` throughout and there is **no outage** —
which makes it a free rehearsal of the exact procedure you are about to run on
the app tunnel, where a mistake is visible.

**Before:**

```bash
connectors "$COMMANDS_TUNNEL_ID"
```

Expected — eight rows, from **both** origin IPs (split since 2026-09-15):

```
a1b2c3d4    92.5.26.74      fra05    2026-09-15T...
b2c3d4e5    92.5.26.74      fra01    2026-09-15T...
c3d4e5f6    98.159.20.40    ord02    2026-09-15T...
d4e5f6a7    98.159.20.40    ord08    2026-09-15T...
... (8 total, roughly 4 per site)
```

**Remove Home's half:**

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" scale deploy/commands-cloudflared --replicas=0
kubectl --kubeconfig="$HOMECFG" -n "$NS" rollout status deploy/commands-cloudflared --timeout=90s
```

Poll until no Home connector remains (`terminationGracePeriodSeconds: 30`;
cloudflared deregisters on SIGTERM, usually within 5–10 s, but the API can lag
by up to a minute):

```bash
for i in $(seq 1 20); do
  echo "--- attempt $i"; connectors "$COMMANDS_TUNNEL_ID"
  connectors "$COMMANDS_TUNNEL_ID" | grep -q '98\.159\.20\.40' || { echo "HOME GONE"; break; }
  sleep 10
done
```

**After:** four rows, every `origin_ip` `92.5.26.74`, every colo `fra*`. No
`98.159.20.40` anywhere. Oracle's `commands-cloudflared` stays at its existing
replica count; do not restart it.

**Rollback (routing only):** `kubectl --kubeconfig="$HOMECFG" -n "$NS" scale
deploy/commands-cloudflared --replicas=1`. But see the point-of-no-return note:
after step 3, Home's origin has no database.

## 5b. App tunnel (`30dde1eb`) — 4–6 min, brief outage on mods/overlay

This tunnel carries:

- `mods.greeniespantry.uk` → `pantry-private-site.pantry-bot.svc:3000`
- `overlay.greeniespantry.uk` → `pantry-overlay.pantry-bot.svc:8080`

It has four connectors, **Home only**. Both hostnames are down between the two
scales — expect 30–90 seconds of 502.

**Before:**

```bash
connectors "$APP_TUNNEL_ID"
```

Expected — four rows, every `origin_ip` `98.159.20.40`, colos `ord*`:

```
1a2b3c4d    98.159.20.40    ord02    2026-09-...
2b3c4d5e    98.159.20.40    ord08    2026-09-...
3c4d5e6f    98.159.20.40    ord12    2026-09-...
4d5e6f7a    98.159.20.40    ord03    2026-09-...
```

If you see any `92.5.26.74` row here, Oracle's `app-cloudflared` is already
running and the tunnel is **already split**. Stop, scale Oracle's to 0, and
restart this step from a clean state.

**Step 1 — remove Home:**

```bash
kubectl --kubeconfig="$HOMECFG" -n "$NS" scale deploy/app-cloudflared --replicas=0
kubectl --kubeconfig="$HOMECFG" -n "$NS" rollout status deploy/app-cloudflared --timeout=90s
```

**Step 2 — confirm zero connectors. Do not skip this.**

```bash
for i in $(seq 1 20); do
  n=$(cf "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$APP_TUNNEL_ID/connections" | jq '.result | length')
  echo "attempt $i: $n connectors"; [ "$n" = "0" ] && break; sleep 10
done
connectors "$APP_TUNNEL_ID"
```

Expected output: `0 connectors`, and `connectors` prints nothing at all. Also
expected at this moment: `mods.greeniespantry.uk` and
`overlay.greeniespantry.uk` return Cloudflare error **1033** (tunnel has no
connectors). That is the correct intermediate state, not a fault.

**Only when the count is 0 — step 3, bring up Oracle:**

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" scale deploy/app-cloudflared --replicas=1
kubectl --kubeconfig="$ORACFG" -n "$NS" rollout status deploy/app-cloudflared --timeout=120s
kubectl --kubeconfig="$ORACFG" -n "$NS" logs deploy/app-cloudflared --tail=30
```

Expected in the log: `Registered tunnel connection` four times, with the tunnel
UUID `30dde1eb-…`. Never print the Deployment environment or the Secret.

**After:**

```bash
connectors "$APP_TUNNEL_ID"
```

Expected — four rows, every `origin_ip` `92.5.26.74`, colos `fra*`, `opened_at`
within the last few minutes. **Zero rows with `98.159.20.40`.** A mixed listing
at this point means Home's connector came back; fix it immediately.

## 5c. Leave these alone

- Tunnel `59569621` (PantryBot): oauth / auth / grafana / operations / cartwise
  → `auth-authentik-server.auth.svc:80`. Authentik and Grafana stay on Home.
  **Do not touch this tunnel, its connectors, or its DNS.**
- Tunnel `8392cd48` (PantryBot-Canada): catch-all 404, tunnel down, zero
  connectors. Out of scope.
- No DNS record changes are required anywhere in this runbook. Both hostnames
  already CNAME to `30dde1eb-….cfargotunnel.com`; the tunnel does not move, only
  which site's connectors serve it. If you find yourself editing DNS, you have
  misread the procedure.

---

# 6. CI/CD — the `homelab-ci` label (15 min; may be deferred)

## The actual situation

Nine jobs in `pantry-bot/.github/workflows/deploy.yml` declare
`runs-on: [self-hosted, homelab-ci]`; one (`propagate-canada`) uses
`canada-ci`. The runners are:

| Runner | OS | Labels | State |
|---|---|---|---|
| `minecraftmachine-pantry-bot` | Linux x86_64, **on Home** | `homelab-ci`, `pantrybot-ci` | online |
| `oracle-pantry-bot` | Linux ARM64, on Oracle | `pantrybot-ci`, `oracle-arm64` | online |
| `truffles-pantry-bot` | Windows, Canada | `canada-ci`, `pantrybot-ci` | online |

**The cutover does not break CI.** Deploys to Oracle already run from the Home
runner using `secrets.KUBE_CONFIG_PANTRYBOT_HA_ORACLE`; the workflow selects
the kubeconfig by `component_target`, not by which machine the job lands on. So
after cutover, pushing to `main` still deploys to Oracle exactly as before.

What is now true is narrower and worth stating plainly: **CI can only deploy
while Home is up.** Home is becoming a cold restore target. If Home is down,
you cannot ship a fix to the site that is actually serving.

## What to change — and what not to

**Do not simply add `homelab-ci` to the Oracle runner.** That is the tempting
one-click fix and it is wrong, for two reasons:

1. `publish-image` runs `docker buildx inspect pantry-multiarch` and **fails
   closed** unless both builder nodes (`pantry-multiarch0`, `pantry-multiarch1`)
   are running. That persistent builder lives on the Home runner; its arm64 node
   *is* Oracle. If the Oracle runner picks up `publish-image`, the builder does
   not exist there and every deploy fails at job 1.
2. Even if it did exist, a multi-arch build on a 4-core Always Free Ampere
   instance would compete for the CPU that is now serving production chat.
   Builds are the one workload you actively want *off* the serving site.

**The correct change is to the workflows, splitting build from deploy:**

1. Add a new label `deploy-ci` to **both** `minecraftmachine-pantry-bot` and
   `oracle-pantry-bot` (GitHub repo → Settings → Actions → Runners → each
   runner → Labels → Add).
2. In `deploy.yml`, change the deploy-side jobs — `deploy-ha`, the rollback
   step, `verify`, `diagnose`, `propagate-oracle`, `propagate-home` — from
   `[self-hosted, homelab-ci]` to `[self-hosted, deploy-ci]`. These jobs only
   run `kubectl` against a kubeconfig written from a secret; either Linux runner
   can do that.
3. Leave `component-contract` and `publish-image` on `[self-hosted, homelab-ci]`.
   They need the multiarch builder, and the contract test is cheap.

Result: a Home outage degrades you from "can build and deploy" to "can deploy
an already-published image tag", instead of "cannot touch production at all".
That is the correct amount of resilience for a free-tier estate, and it costs
one label plus six edited lines.

**Follow-up, not now:** if you want builds to survive a Home outage too, stand
up a second buildx builder whose amd64 node is something other than
minecraftmachine, or accept arm64-only emergency builds — Oracle is arm64 and
is the only site that serves. File it; do not do it during the cutover window.

**Rollback:** revert the six `runs-on` lines. The `deploy-ci` label is inert if
nothing references it.

---

# 7. Verification (20–25 min)

Work down the list. Anything that fails, fix before you call the cutover done.

## 7.1 Database

```bash
opsql "select pg_is_in_recovery()"                          # f
opsql "select current_setting('default_transaction_read_only')"  # off
opsql "select timeline_id from pg_control_checkpoint()"     # previous + 1
opsql "select count(*) from pg_stat_replication"            # 0 — nothing replicates from Oracle now
kubectl --kubeconfig="$HOMECFG" -n "$NS" get statefulset postgres-authority-standby-home-canada
                                                             # READY 0/0
```

## 7.2 Single-writer interlocks

```bash
opsql "select resource, owner_id, epoch, lease_until > now() as valid
       from pantry_leases order by resource"
```

Expected: eight resources, every `owner_id` prefixed `oracle-`, every `valid`
`t`, epochs stable or incrementing slowly. **No `home-` owner may be renewing.**

Run it twice, 60 seconds apart. `lease_until` should move forward for each
resource; `owner_id` should not change. An `owner_id` that flaps between two
values means two pods are fighting for the lease — check replica counts.

## 7.3 Workloads

```bash
kubectl --kubeconfig="$ORACFG"  -n "$NS" get deploy
kubectl --kubeconfig="$ORACFG"  -n "$NS" get pods -o wide
kubectl --kubeconfig="$HOMECFG" -n "$NS" get deploy
```

Expected: on Oracle, every Deployment at its intended count and fully Ready,
every pod on node `pantry-bot-oracle`, zero restarts. On Home, **every**
PantryBot Deployment at `0/0` — including both cloudflared Deployments.

Note: readiness reports process health, not lease ownership. A non-owning
replica is Ready and idle; that is by design, not a fault.

## 7.4 Routing

```bash
connectors "$APP_TUNNEL_ID"        # 4 rows, all 92.5.26.74 / fra*
connectors "$COMMANDS_TUNNEL_ID"   # 4 rows, all 92.5.26.74 / fra*
```

Zero rows with `98.159.20.40` on either. Then from outside the network
(phone hotspot, not the home LAN):

```bash
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://mods.greeniespantry.uk/
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://overlay.greeniespantry.uk/
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://commands.greeniespantry.uk/
curl -sS https://commands.greeniespantry.uk/api/public/commands | head -c 200
```

Expected: `200` for all three. Expect `time_total` to rise relative to Home —
Oracle is further away; that is the accepted cost of consolidation.

## 7.5 Authentik and Grafana must be untouched

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://auth.greeniespantry.uk/
curl -sS -o /dev/null -w '%{http_code}\n' https://grafana.greeniespantry.uk/
kubectl --kubeconfig="$HOMECFG" -n auth get deploy
```

Expected: `200`/`302` as before, Authentik still running on Home, tunnel
`59569621` connectors unchanged. If these broke, you touched the wrong tunnel.

Then actually log in through Authentik once. It is a 12-round-trip flow and a
`200` on the landing page proves nothing.

## 7.6 The bot itself — the test that matters

In the live Twitch chat:

1. Send one command that produces a reply.
   **Expected: exactly one reply.** Two replies means two gateways or two
   dispatchers are live — stop and find the second one.
2. Check the overlay renders the resulting event, once.
3. Confirm the gateway log shows exactly one IRC connection and one channel
   join, and no reconnect loop:

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" logs deploy/pantry-twitch-gateway --tail=200 \
  | grep -iE 'connect|join|lease|reconnect' | tail -30
```

Queue and outbox are draining, not growing:

```bash
opsql "select status, count(*) from pantry_outbox group by 1 order by 1"
sleep 60
opsql "select status, count(*) from pantry_outbox group by 1 order by 1"
```

Expected: `pending` flat or falling; `failed` not climbing. A rising `pending`
means the dispatcher is not claiming — check its lease and its logs.

## 7.7 Backup is running (do not close the window without this)

The cutover removes the last hot copy of this database. Home is now a cold
target holding a `Retain`ed PVC on an old timeline, which is not a backup.

```bash
kubectl --kubeconfig="$ORACFG" -n "$NS" get cronjob
kubectl --kubeconfig="$ORACFG" -n "$NS" create job --from=cronjob/<backup-cronjob> \
  backup-smoke-$(date +%s)
kubectl --kubeconfig="$ORACFG" -n "$NS" logs job/backup-smoke-<...>
rclone size r2:pantry-bot-backups/recovery/pantrybot/ --s3-no-check-bucket
```

If no backup CronJob exists on Oracle yet, that is the top-priority follow-up
and it should be done the same day — clone
`scripts/authentik-postgres-backup.sh`, which is already proven, rather than
designing something new.

---

# 8. Follow-ups (after the window, in order)

1. **Backup CronJob on Oracle**, same day. Clone
   `scripts/authentik-postgres-backup.sh`; take `k8s/ha/base/postgres-analyze-cronjob.yaml`
   as the CronJob shape, since it already sources `PANTRY_DATABASE_URL` from
   `pantry-bot-platform` and therefore follows the writer.
2. **R2 retention.** No uploader in this fleet prunes remote objects. Add a
   bucket lifecycle rule before the free allowance is exceeded.
3. **Enable the external freshness monitor.** `MONITOR_R2_PREFIXES` on the GCP
   `homelab-external-monitor` already implements this; it needs one read-only,
   bucket-scoped R2 token. Add `pantrybot=recovery/pantrybot/:<max_age>`. It is
   the only verification layer that survives "Oracle is gone".
4. **Decide the maintenance responder** (step 1.5). Move it to Oracle or accept
   that the bot has no down-voice.
5. **CI runner labels** (step 6), if deferred.
6. **`archive_mode` / PITR.** It is set nowhere in either repository and is
   `PGC_POSTMASTER` — enabling it restarts PostgreSQL. If it was not folded
   into this window, it will cost a second outage. See the backup design doc.
7. **Fencing removal** — the witness, promoter and fencer trees. **Not now.**
   Do not remove a safety device during the manoeuvre it exists for; the
   ordering constraints are documented separately and start with making the
   witness optional in `src/runtime/overlayProcess.ts`, which currently
   *requires* `PANTRY_WITNESS_URL` and crash-loops without it.
8. **Delete the legacy non-HA `k8s/*.yaml` manifests.** `k8s/gateway.yaml`
   binds `PANTRY_INSTANCE_ID` from a Secret — one fixed string shared by every
   replica — so applying it with `replicas: 2` would let both gateway pods hold
   the same lease. Its header says rehearsal-only; it is still a loaded gun.
9. **Home's old PVC.** Keep it until at least two verified Oracle backups
   exist and one has been test-restored. Then reclaim it.
