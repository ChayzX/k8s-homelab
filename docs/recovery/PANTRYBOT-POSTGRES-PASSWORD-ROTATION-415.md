# Oracle PantryBot superuser password rotation (issue #415)

Run only in a dedicated maintenance window with enough time for two sequential
application rollouts and both backup/ANALYZE checks in each phase. Avoid the
daily 05:10 UTC backup and Sunday 04:23 UTC ANALYZE. #414 is now live and
verified; do not restore Home PantryBot runtime or retired replication for
this procedure.

## Credential and client inventory (2026-10-10, values omitted)

| Holder | Current use | Rotation action |
| --- | --- | --- |
| Oracle `pantry-bot/pantry-bot-platform`, key `PANTRY_DATABASE_URL` | Database URL for worker (2), gateway, dispatcher, maintenance responder, overlay (2), private API (2), backup init container, and ANALYZE CronJob | Stage alternate URL, replace this one key, restart six deployments; new Job pods read the latest Secret |
| Oracle `pantry-bot/pantry-bot-postgres-standby`, key `POSTGRES_PASSWORD` | StatefulSet `envFrom`; historical bootstrap value differs from the working application URL password | Align with the final new `pantry` password only after `ALTER ROLE`; changing this Secret alone does **not** change the role on the existing PVC |
| Home `observability/grafana-postgres-pantry`, key `password` | Grafana datasource uses dedicated non-superuser role `pantry_grafana`; fresh health check passed after #412 | Keep unchanged; it is independent of the `pantry` superuser password |
| Home `pantry-bot/pantry-bot-platform`, key `PANTRY_DATABASE_URL` | Retired Secret with the same current password and an obsolete Home database hostname; no live Home workload references found | Delete only after rechecking no consumers and confirming Oracle-only recovery policy |
| PostgreSQL `pantry` role | Current superuser; the password verifier lives in the database | Rotate through the local Unix socket after all serving clients move to a temporary role |

The Oracle application and bootstrap Secrets, the retired Home application
Secret, and the Home Grafana Secret also have
`kubectl.kubernetes.io/last-applied-configuration` annotations that contain
encoded Secret data snapshots. The Oracle application's annotation differs
from its current URL. Treat those annotations as additional historical
credential copies. The helper below removes the annotation when it replaces
Oracle Secrets. #414 removed it from the Home Grafana Secret; deleting the
retired Home Secret removes its snapshot.

The scan compared decoded values **in memory** across Kubernetes Secrets on
both clusters and emitted only matching object/key names. It cannot account
for external password managers, local files, old backups, screenshots, or
copies outside Kubernetes. Do not print or paste Secret values into a tool
transcript, shell command, issue, log, or Git. Historic completed Jobs retain
Secret references, not a runnable copy of the old environment. The app repo
also has ad hoc maintenance/import scripts that accept `PANTRY_DATABASE_URL`
from an operator's environment; they are not running Kubernetes workloads,
but any scheduled copy or operator vault entry must be inventoried and
updated. A `pg_stat_activity` snapshot showed only the six serving role
Deployments and local `psql`, but transient/admin clients may be absent from
one snapshot. The local user's crontab, system cron files, and user systemd
units were checked; no PantryBot database URL holder was found. Oracle has no
active PantryBot promoter service or PantryBot-specific scheduled unit. The
operator confirmed there is no password vault, and no command-line password
manager was found. Oracle K3s reports `Encryption Status: Disabled, no
configuration file found`; Kubernetes Secret data therefore is not an
approved encrypted vault for a human recovery copy. The external vault-holder
inventory is complete (none), but **do not rotate until a secure, recoverable
credential store is established and the new value can be retained there
without exposing it in a transcript or repository.**

The `PANTRYBOT_POSTGRES_PASSWORD` Actions secret in `ChayzX/k8s-homelab` had
no references in current workflows or repository files and was deleted after
that check. Do not recreate it unless a reviewed workflow needs it; any new
workflow must receive the final value through the approved secret-management
path. `ChayzX/pantry-bot` has no Actions database password secret.

The six database-consuming Deployments are `pantry-chat-worker`,
`pantry-twitch-gateway`, `pantry-twitch-dispatcher`,
`pantry-maintenance-responder`, `pantry-overlay-delivery`, and
`pantry-private-api`. Commands site, private site, and Cloudflare tunnels do
not inject `PANTRY_DATABASE_URL`. Backup runs daily at **05:10 UTC** and
ANALYZE weekly Sunday at **04:23 UTC**; check the local/UTC conversion for
the chosen window. The responder uses a `Recreate` strategy and pauses while
it restarts. Gateway and dispatcher use surge rollouts with database leases.

## Gates before opening a rotation window

0. Establish an approved secure credential store with a tested recovery path.
   Do not treat the current unencrypted-at-rest Kubernetes Secrets as a human
   password vault. The operator confirmed there is no external vault today.
1. Complete #414: a live Grafana query must report `current_user =
   pantry_grafana`, the datasource must be healthy, all PantryBot panels must
   work, and the Home `pantry` HBA rule must have been removed. The #412 HBA
   candidate initially includes both roles for migration; do not leave that
   old Home `pantry` rule in place for this rotation.
2. Run #412's disposable R2 restore Job against the **latest** dump, record
   table count and size, then delete its scratch namespace. The prior
   2026-10-09 dump restored successfully, but a new dump should be tested
   before rotating.
3. Confirm every listed Deployment is Ready, no backup or ANALYZE Job is
   active, and a local operator can connect with `kubectl exec ... psql -U
   pantry -d pantry` through the Unix socket. Keep that path available for
   rollback. Record current HBA, role metadata, and Secret **key names** only.
4. If #412's narrow HBA is live, temporarily add
   `host pantry pantry_rotation_415 10.42.0.0/24 scram-sha-256` using its
   atomic HBA update/rollback procedure. Confirm a fresh probe from the
   pod CIDR can authenticate before changing any serving Secret. Remove the
   rule after the temporary role is retired.
5. Reserve enough time for two full Deployment rollouts and verification,
   two fresh maintenance Jobs, Grafana checks, and rollback. Avoid a window
   that overlaps either CronJob schedule.

## Credential staging without values in arguments or local files

Copy the checked-in [Secret helper](../../scripts/pantrybot-secret-update-415.py)
and [probe Job template](pantrybot-password-probe-415.yaml.tpl) to Oracle.
The helper uses a hidden terminal prompt and sends a minimal Kubernetes
Secret object through `kubectl` standard input. It preserves unrelated keys,
labels, and annotations on `pantry-bot-platform`. It refuses unexpected URL
host, database, port, or role. It changes Kubernetes Secrets only; it does
not change a PostgreSQL role or restart a pod.

```sh
scp -o BatchMode=yes scripts/pantrybot-secret-update-415.py oracle:/tmp/pantrybot-secret-update-415.py
scp -o BatchMode=yes docs/recovery/pantrybot-password-probe-415.yaml.tpl oracle:/tmp/pantrybot-password-probe-415.yaml.tpl
ssh -t oracle
python3 /tmp/pantrybot-secret-update-415.py snapshot-old
```

`snapshot-old` creates `pantry-bot-db-url-old-415` inside Oracle Kubernetes,
without exporting the old URL. Keep it until `stage-final` and the final
credential probe succeed; the helper reads this Secret to reject password
reuse. Delete it immediately before promoting the final URL. Preserve a
recovery copy only in the approved secure credential store; do not use this
temporary Secret as long-term storage.

Generate two distinct new passwords in the approved password manager. Use
one for a short-lived `pantry_rotation_415` superuser and the other for the
final `pantry` role. In the database container's interactive `psql` session,
create the temporary role without login, set its password using psql's hidden
`\password` prompt, then enable login:

```sh
sudo k3s kubectl -n pantry-bot exec -it pantry-postgres-0 -- psql -U pantry -d pantry
CREATE ROLE pantry_rotation_415 NOLOGIN SUPERUSER;
\password pantry_rotation_415
ALTER ROLE pantry_rotation_415 LOGIN;
\q
python3 /tmp/pantrybot-secret-update-415.py stage-temp
```

The `stage-temp` prompt takes a percent-encoded PostgreSQL URL using
`pantry_rotation_415`; it creates `pantry-bot-db-url-temp-415` with the URL
and separate `PG*` fields for a password-free process argument probe. It
rejects reuse of the staged old password. Run the one-shot probe and require
`current_user = pantry_rotation_415`, database `pantry`, and
`pg_is_in_recovery() = f`:

```sh
sed 's/@STAGE@/temp/g' /tmp/pantrybot-password-probe-415.yaml.tpl | sudo k3s kubectl create -f -
sudo k3s kubectl -n pantry-bot wait --for=condition=complete job/pantry-db-probe-temp-415 --timeout=2m
sudo k3s kubectl -n pantry-bot logs job/pantry-db-probe-temp-415
sudo k3s kubectl -n pantry-bot delete job pantry-db-probe-temp-415
```

## Two-phase switch

1. Promote the temporary URL with `python3
   /tmp/pantrybot-secret-update-415.py promote-temp`. Kubernetes environment
   variables do not reload in existing pods. Restart the six Deployments
   **one at a time**, waiting for each rollout to finish and for fresh
   database connections from every replica. Suggested order: private API,
   overlay, worker, responder, dispatcher, gateway. The responder's one-pod
   `Recreate` rollout has a short service pause. A failed rollout at this
   point can return to the staged old URL with `promote-old` and another
   rollout; the original `pantry` password has not changed yet.
2. Confirm no serving pod still uses `pantry`, no backup/ANALYZE Job is
   active, and Home Grafana uses `pantry_grafana`. Use a fresh Job from each
   CronJob to prove the temporary URL works for transient pods. The backup
   must leave a new verified R2 object. Do not proceed if any client is
   missing or if the temporary role cannot do normal app work.
3. Through the Oracle PostgreSQL container's local `psql` socket, run
   `\password pantry` and enter the final password twice at the hidden
   prompt. Existing sessions do not prove the new password; use `stage-final`
   with the new percent-encoded `pantry` URL, run the final one-shot probe
   below, and require success before changing app Secrets. **Keep
   `pantry-bot-db-url-old-415` until `stage-final` has succeeded.** The helper
   reads that Secret to reject password reuse. Delete it only after the final
   probe succeeds and immediately before promoting the final URL.
4. Promote the final URL with `promote-final`, restart the same six
   Deployments sequentially, and verify fresh connections from every
   replica. Run fresh backup and ANALYZE Jobs again, verify the new R2
   object and Grafana panels, and check logs for authentication failures.
5. Run `set-bootstrap-password` with the same final password. The helper
   refuses a value that differs from the final staged URL. This aligns
   the StatefulSet's bootstrap Secret for future recovery; it does not
   rotate the live role a second time. Do not restart PostgreSQL merely
   to reread that environment variable.

Example rollout and fresh-connection check for one Deployment:

```sh
sudo k3s kubectl -n pantry-bot rollout restart deployment/pantry-private-api
sudo k3s kubectl -n pantry-bot rollout status deployment/pantry-private-api --timeout=5m
sudo k3s kubectl -n pantry-bot get pods -o wide
sudo k3s kubectl -n pantry-bot exec <new-pod-name> -- node -e '
  const {Client}=require("pg");
  const c=new Client({connectionString:process.env.PANTRY_DATABASE_URL,connectionTimeoutMillis:5000});
  c.connect().then(()=>c.query("SELECT current_user"))
    .then(r=>console.log(r.rows[0].current_user))
    .catch(e=>{console.error(e.message);process.exitCode=1})
    .finally(()=>c.end());
'
```

Repeat the connection check for **every new pod**, not just one replica or
an existing pooled session. For the final probe:

```sh
python3 /tmp/pantrybot-secret-update-415.py stage-final
sed 's/@STAGE@/final/g' /tmp/pantrybot-password-probe-415.yaml.tpl | sudo k3s kubectl create -f -
sudo k3s kubectl -n pantry-bot wait --for=condition=complete job/pantry-db-probe-final-415 --timeout=2m
sudo k3s kubectl -n pantry-bot logs job/pantry-db-probe-final-415
sudo k3s kubectl -n pantry-bot delete job pantry-db-probe-final-415
```

Run fresh maintenance Jobs only when the matching CronJob has no active Job:

```sh
phase=temp  # use final after the final rollout
sudo k3s kubectl -n pantry-bot create job "postgres-backup-415-$phase" --from=cronjob/postgres-backup
sudo k3s kubectl -n pantry-bot wait --for=condition=complete "job/postgres-backup-415-$phase" --timeout=15m
sudo k3s kubectl -n pantry-bot logs "job/postgres-backup-415-$phase" --all-containers=true
sudo k3s kubectl -n pantry-bot create job "postgres-analyze-415-$phase" --from=cronjob/postgres-analyze
sudo k3s kubectl -n pantry-bot wait --for=condition=complete "job/postgres-analyze-415-$phase" --timeout=10m
sudo k3s kubectl -n pantry-bot logs "job/postgres-analyze-415-$phase"
```

Run this block once per phase with its distinct Job names. No `psql` command
should include a password-bearing URL as a command-line argument.

## Rollback and retirement

Before `\password pantry`, `promote-old` plus six sequential restarts
returns to the old credential. After `\password pantry`, the old credential
is revoked and **must not** be restored. If the final phase fails, use
`promote-temp` and six sequential restarts while the temporary role remains
valid. If the temporary role itself proves unsuitable, use the Oracle local
socket to set a different new `pantry` password and stage another final URL;
never reinstate the potentially exposed old password.

Keep the temporary role and its Secret available through a short monitored
stability period. Then inspect objects it may own, reassign any such objects
to `pantry` under review, disable login/superuser, and drop the role only
after no clients use it. Delete the remaining `temp` and `final` staged URL
Secrets and the probe Jobs. Verify the retired Home `pantry-bot-platform`
Secret still has no
consumers, then delete it. Confirm #414 replaced the Home Grafana password
Secret and datasource; remove any old credential in the approved vault or
other known stores. Finally remove the temporary role's HBA rule and verify
the application, backup, ANALYZE, and Grafana paths once more.

The old `pantry` verifier in database snapshots remains in retained backups;
retention and restore procedures must account for that. Do not destroy a
valid recovery point simply to erase an old verifier. On restore, rotate
credentials before exposing the restored instance to clients.
