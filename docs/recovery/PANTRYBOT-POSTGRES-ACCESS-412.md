# Oracle PantryBot PostgreSQL access (issue #412)

The primary runs on Oracle from a retained local-path PVC. Its `pg_hba.conf`
lives inside that PVC; `kubectl apply` of `30-postgres-primary.yaml` cannot
update it. On 2026-10-10 UTC, the broad network rule and retired
replication allowances were replaced with the candidate below. The prior file
is retained on the PVC as `pg_hba.conf.pre-412` for rollback. Do not use any
retired Home/Canada replication procedure.

## Verified client inventory (2026-10-10 UTC)

| Client | Route and observed source | Required after restriction |
| --- | --- | --- |
| Worker (2), gateway, dispatcher, responder, overlay (2), private API (2) | `pantry-postgres.pantry-bot.svc.cluster.local:5432`; active connections from Oracle pod IPs `10.42.0.142` through `.177` | Yes; allow Oracle node pod CIDR `10.42.0.0/24` |
| Commands site, private site, Cloudflare tunnels | These deployment specs have no `PANTRY_DATABASE_URL` | No direct database path |
| Backup and ANALYZE CronJobs | Same `PANTRY_DATABASE_URL` Secret and Service; most recent backup pod was `10.42.0.164` | Yes; allow Oracle pod CIDR |
| Home Grafana `PantryPostgres` datasource | Direct to Oracle tailnet `100.78.181.15:5432`; the fresh datasource health check after the HBA reload returned `Database Connection OK`. Provisioning uses dedicated role `pantry_grafana` from `100.84.89.87/32` | Yes; allow only `pantry_grafana` from MinecraftMachine `100.84.89.87/32` |
| Database readiness and operator `kubectl exec` | Unix socket as `pantry` in the database container | Yes; retain local `pantry` trust |
| Admin via Oracle SSH | Operator can use `kubectl exec` and the local socket. Direct tailnet admin clients have not been inventoried and are **not** in the proposed allowlist | Use the documented local path; check for any needed external client before rollout |
| Monitoring other than Grafana | Oracle Prometheus scrapes Kubernetes/job metrics; no direct PostgreSQL query path found | No database allowlist entry |
| Home/Canada replication | `pg_stat_replication` and replication slots empty; PantryBot is Oracle-only | No; remove old HBA entries |

Grafana's move to the dedicated `pantry_grafana` role is complete under
[issue #414](https://github.com/ChayzX/k8s-homelab/issues/414). The live HBA
allows no Home access as `pantry`; the successful datasource health check
confirms the remaining role and route. Superuser rotation remains tracked in
[issue #415](https://github.com/ChayzX/k8s-homelab/issues/415).

Oracle's single k3s node reports `podCIDR=10.42.0.0/24`. The current primary is
`hostNetwork: true`, listens on IPv4/IPv6 wildcard addresses, and has
the prior HBA included `host all all all scram-sha-256` and five replication
allowances. They are now replaced by the exact candidate below. A fresh backup
created at 2026-10-10 20:52 UTC uploaded to R2 and restored in isolation at
20:58 UTC: 64 public tables, 41,778,199 database bytes, PostgreSQL 16.15. The
scratch namespace and its copied R2 credential Secret were deleted.

## Gate 1: prove the current dump restores

During the maintenance preflight, inspect the newest backup job and run the
disposable [restore-check Job](pantrybot-postgres-restore-check-412.yaml) on
Oracle. It downloads the newest timestamped PantryBot dump from R2, restores
it into an `emptyDir` PostgreSQL 16 instance with network listening disabled,
and reports restored table count and database bytes. Run it in a separate
`pantry-bot-restore-412` namespace with only the two R2 Secret keys copied
there. The Job has no production database URL/password or PVC mount; all SQL
uses a Unix socket in its scratch volume. Delete the scratch namespace on
success or failure, which also removes its Secret and Job.

```sh
scp -o BatchMode=yes docs/recovery/pantrybot-postgres-restore-check-412.yaml oracle:/tmp/pantrybot-postgres-restore-check-412.yaml
ssh -o BatchMode=yes oracle
```

In the Oracle shell, run this block. The exit trap prints Job logs when
available and removes the namespace even if the download or restore fails.

```sh
set -e
sudo k3s kubectl -n pantry-bot get cronjob postgres-backup postgres-analyze
sudo k3s kubectl -n pantry-bot get jobs --sort-by=.metadata.creationTimestamp
sudo k3s kubectl create namespace pantry-bot-restore-412
trap 'sudo k3s kubectl -n pantry-bot-restore-412 logs job/pantry-postgres-restore-check-412 --all-containers=true || true; sudo k3s kubectl delete namespace pantry-bot-restore-412 --ignore-not-found --wait=true' EXIT
# Keep credential values in the pipe; do not enable shell tracing.
sudo k3s kubectl -n pantry-bot get secret pantry-bot-litestream -o json \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); keys=("LITESTREAM_ACCESS_KEY_ID","LITESTREAM_SECRET_ACCESS_KEY"); print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":"pantry-bot-litestream","namespace":"pantry-bot-restore-412"},"type":"Opaque","data":{k:s["data"][k] for k in keys}}))' \
  | sudo k3s kubectl apply -f -
sudo k3s kubectl create -f /tmp/pantrybot-postgres-restore-check-412.yaml
sudo k3s kubectl -n pantry-bot-restore-412 wait --for=condition=complete job/pantry-postgres-restore-check-412 --timeout=15m
sudo k3s kubectl -n pantry-bot-restore-412 logs job/pantry-postgres-restore-check-412 --all-containers=true
sudo k3s kubectl delete namespace pantry-bot-restore-412
trap - EXIT
```

**Rehearsal completed 2026-10-10 04:10 UTC:** the isolated Job restored
`pantry-20261009T051003Z.dump.gz` into PostgreSQL 16.15 and reported 63
public ordinary/partitioned tables and 40,893,463 database bytes. The live
database had 63 such tables and occupied 45,915,159 bytes at inventory time;
the difference is expected after a logical restore. The scratch namespace,
Job, Secret, and `emptyDir` were deleted. Repeat this gate against a current
dump immediately before the coordinated HBA change. A failed or implausibly
small restore blocks that change.

## Gate 2: narrow `pg_hba.conf` during a coordinated window

The live [candidate HBA file](../../pantry-bot/31-postgres-pg-hba.conf) allows
the local `pantry` socket, `pantry` from the Oracle pod CIDR, and
`pantry_grafana` from MinecraftMachine. It rejects all remote replication and
all other TCP connections, including localhost TCP on the host-networked
Oracle node. The current `pg_hba_file_rules` error count is zero.

From a workstation with this checkout, copy the candidate to Oracle, then run
the remaining commands on Oracle. Preserve the original file on the retained
PVC. `kubectl exec` currently runs as UID 0, and the HBA owner is
`postgres:root` mode `600`; verify those facts again first.

```sh
scp -o BatchMode=yes pantry-bot/31-postgres-pg-hba.conf oracle:/tmp/pantrybot-pg-hba-412.conf
ssh -o BatchMode=yes oracle
sudo k3s kubectl get node pantry-bot-oracle -o jsonpath='{.spec.podCIDR}'
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- sh -c 'id -u; stat -c "%U:%G %a" /var/lib/postgresql/data/pg_hba.conf'
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- psql -U pantry -d pantry -AtX -c 'SELECT type,database,user_name,address,auth_method,error FROM pg_hba_file_rules ORDER BY rule_number'
sudo k3s kubectl -n pantry-bot cp /tmp/pantrybot-pg-hba-412.conf pantry-postgres-0:/tmp/pg_hba.conf.candidate
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- sh -ec '
  cd /var/lib/postgresql/data
  test ! -e pg_hba.conf.pre-412
  cp -p pg_hba.conf pg_hba.conf.pre-412
  cp /tmp/pg_hba.conf.candidate pg_hba.conf.next
  chown postgres:root pg_hba.conf.next
  chmod 600 pg_hba.conf.next
  mv -f pg_hba.conf.next pg_hba.conf
'
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- psql -U pantry -d pantry -AtX -c 'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL'
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- psql -U pantry -d pantry -AtX -c 'SELECT pg_reload_conf()'
```

The rollout preserved `/var/lib/postgresql/data/pg_hba.conf.pre-412` with
owner `postgres:root`, mode `600`; the replacement has the same owner/mode.
The pre-reload parser error count was zero, `pg_reload_conf()` returned `t`,
and a post-reload parser query also returned zero. Existing sessions may
continue, so verify **new** connections from each path:

1. All nine serving pods across the six DB-using deployments made a fresh
   connection and returned `current_user=pantry`, `current_database=pantry`,
   and `pg_is_in_recovery=false`.
2. A fresh backup Job completed, wrote and verified
   `pantry-20261010T205241Z.dump.gz` in R2 (3,734,553 bytes). A fresh ANALYZE
   Job completed with zero tables lacking statistics.
3. Home Grafana's fresh `PantryPostgres` health check returned
   `Database Connection OK` using `pantry_grafana`. A credential-free
   PostgreSQL startup probe from MinecraftMachine using the disallowed
   `pantry` role received `pg_hba.conf rejects connection`. A second
   credential-free startup probe over Oracle host TCP loopback
   (`127.0.0.1:5432`) as `pantry` also received `pg_hba.conf rejects
   connection`, confirming localhost TCP is denied while Unix-socket recovery
   remains available. No separate external non-allowlisted host was available.
4. Local socket `SELECT 1` succeeded. A denied tailnet PostgreSQL client test
   was not performed.
5. Prometheus queries and the Grafana dashboard query checks remain covered
   by the release QA report. The dashboard visual browser check is separate.

For future validation, run `SELECT 1` through each pod of the six DB-using deployments:
   `pantry-chat-worker`, `pantry-twitch-gateway`, `pantry-twitch-dispatcher`,
   `pantry-maintenance-responder`, `pantry-overlay-delivery`, and
   `pantry-private-api`. An example for one pod is below. Repeat for both
   replicas where present, and monitor deployment readiness and connection
   errors for at least a full retry cycle.
2. Run a fresh backup Job and a fresh ANALYZE Job from their CronJobs, then
   confirm success and the new R2 object. This verifies their transient pod
   addresses, which may differ from inventory time.
3. Query the Home Grafana `PantryPostgres` datasource and its PantryBot SQL
   panels; confirm role `pantry_grafana` and source `100.84.89.87/32`.
4. Verify operator local-socket access and confirm a non-allowlisted tailnet
   host cannot authenticate. Observe `pg_stat_activity` and database logs.

```sh
sudo k3s kubectl -n pantry-bot exec <app-pod-name> -- node -e '
  const {Client}=require("pg");
  const c=new Client({connectionString:process.env.PANTRY_DATABASE_URL,connectionTimeoutMillis:5000});
  c.connect().then(()=>c.query("SELECT 1")).then(()=>console.log("new DB connection OK"))
    .catch(e=>{console.error(e.message);process.exitCode=1}).finally(()=>c.end());
'
sudo k3s kubectl -n pantry-bot create job postgres-backup-412 --from=cronjob/postgres-backup
sudo k3s kubectl -n pantry-bot wait --for=condition=complete job/postgres-backup-412 --timeout=15m
sudo k3s kubectl -n pantry-bot logs job/postgres-backup-412 --all-containers=true
sudo k3s kubectl -n pantry-bot create job postgres-analyze-412 --from=cronjob/postgres-analyze
sudo k3s kubectl -n pantry-bot wait --for=condition=complete job/postgres-analyze-412 --timeout=10m
sudo k3s kubectl -n pantry-bot logs job/postgres-analyze-412
```

If any required path fails, restore the original HBA **before** changing
anything else. The backup is on the same retained PVC:

```sh
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- sh -ec '
  cd /var/lib/postgresql/data
  test -s pg_hba.conf.pre-412
  cp -p pg_hba.conf.pre-412 pg_hba.conf.rollback
  mv -f pg_hba.conf.rollback pg_hba.conf
'
sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- psql -U pantry -d pantry -AtX -c 'SELECT pg_reload_conf()'
```

Do not remove `pg_hba.conf.pre-412` until a later reviewed cleanup. A
Tailscale ACL or Oracle host firewall rule for TCP 5432 can then narrow the
network edge to MinecraftMachine, but must preserve cluster pod traffic and
be tested independently. The HBA change alone does not close the TCP listener.

## Separate maintenance: remove `hostNetwork`

This is a distinct database restart and service migration. The retained
local-path PVC must stay on the Oracle node. Before changing the StatefulSet,
move Home Grafana off direct `100.78.181.15:5432` to a tested, authenticated
path to `pantry-postgres.pantry-bot.svc.cluster.local`; otherwise removing
`hostNetwork` breaks its panels. Confirm the path's source address, add it to
HBA if required, test a new Grafana query, then remove the old direct address.
Preserve the Service's role-only selector. Plan a database outage window,
capture rollback manifests and HBA, verify the PVC mount and pod scheduling,
then roll one change at a time. If readiness or clients fail, restore the
prior StatefulSet and Grafana path while retaining the same PVC; do not create
a second writer.
