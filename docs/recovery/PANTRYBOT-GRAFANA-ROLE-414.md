# PantryBot Grafana database role (#414)

Run this during the coordinated #412 Oracle PostgreSQL access window. Home
Grafana remains the monitoring client. This change does not migrate PantryBot
runtime away from Oracle.

## Observed state and required order

On 2026-10-10 the `PantryPostgres` datasource connected from
`100.84.89.87/32` as `pantry`, which is a database superuser. The checked-in
`pantry-bot.json` has five SQL targets. The **live** Home ConfigMap has
`pantry-bot-usage.json` with eight SQL targets, which is not in the checked-in
dashboard sources at this commit. Its live `pantry-bot.json` differs from the
checked-in health dashboard and currently has no SQL targets. The eight live
usage queries and five checked-in health queries together need seven `public`
tables:

| Table | Columns required by live SQL |
| --- | --- |
| `pantry_events` | `created_at`, `source`, `event_type` |
| `engagement_actions` | `at`, `source`, `reason`, `action` |
| `custom_commands` | `name`, `enabled`, `category`, `permission_level`, `source`, `use_count`, `created_at` |
| `engagement_message_counts` | `day`, `status`, `count` |
| `community_actions` | `at`, `action` |
| `pantry_outbox` | `status` |
| `community_participants` | `user_id` |

All seven are ordinary tables without row security. The role gets column
SELECT grants only. It does **not** get `custom_commands.response_template`,
`community_actions.user_id`, `engagement_actions.user_id`, or event payloads.
There are no future-table default grants; review privileges whenever a panel
adds a table or column. Before deployment, confirm the live Grafana ConfigMap
has not gained another SQL dashboard or query since this inventory.
Import the live usage dashboard into the release's dashboard source and
generated ConfigMap before running `grafana-deploy.yml`; otherwise that
workflow's dashboard ConfigMap apply can lose or drift the usage view.

Before switching Grafana, merge this line into #412's candidate
`pantry-bot/31-postgres-pg-hba.conf`, immediately next to the temporary
`pantry` rule for the same source:

```text
host    pantry          pantry_grafana  100.84.89.87/32         scram-sha-256
```

Keep the `pantry` Home rule until the new datasource has passed every check.
After that, remove only the Home `pantry` line, reload PostgreSQL, and verify
Grafana again. Retain the Oracle pod-CIDR `pantry` rule for the application and
CronJobs. The #412 HBA backup and rollback procedure applies throughout.

## Stage role and credential

From a trusted checkout, use a shell with tracing disabled. Do not print the
password or paste it into a command argument, issue, log, or Git file.

```sh
set -euo pipefail
umask 077
secret_file=$(mktemp)
old_secret_file=$(mktemp)
openssl rand -hex 32 | tr -d '\n' > "$secret_file"

# Preserve only the current Home Secret's identity, type, and data for
# rollback. Do not copy metadata annotations into the backup: the live Secret
# currently has a client-side last-applied annotation containing an older
# encoded credential snapshot. Treat this file as a credential.
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability get secret grafana-postgres-pantry -o json' \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":s["metadata"]["name"],"namespace":s["metadata"]["namespace"]},"type":s["type"],"data":s["data"]}))' \
  > "$old_secret_file"

# Migration is idempotent and leaves a newly created role without LOGIN.
ssh -o BatchMode=yes oracle \
  'sudo -n k3s kubectl -n pantry-bot exec -i pantry-postgres-0 -- psql -X -v ON_ERROR_STOP=1 -U pantry -d pantry' \
  < observability/pantry-grafana-role.sql

# Password is sent on stdin to psql; the role name is fixed and the generated
# hexadecimal password has no SQL metacharacters.
printf "ALTER ROLE pantry_grafana LOGIN PASSWORD '%s';\n" "$(cat "$secret_file")" \
  | ssh -o BatchMode=yes oracle \
      'sudo -n k3s kubectl -n pantry-bot exec -i pantry-postgres-0 -- psql -X -v ON_ERROR_STOP=1 -U pantry -d pantry' \
      > /dev/null

# Verify grants and forbidden capabilities without printing any secret.
ssh -o BatchMode=yes oracle \
  'sudo -n k3s kubectl -n pantry-bot exec -i pantry-postgres-0 -- psql -X -At -U pantry -d pantry' <<'SQL'
SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin
FROM pg_roles WHERE rolname='pantry_grafana';
SELECT table_name,column_name,privilege_type
FROM information_schema.column_privileges
WHERE table_schema='public' AND grantee='pantry_grafana'
ORDER BY table_name,column_name,privilege_type;
SELECT has_table_privilege('pantry_grafana','public.custom_commands','SELECT'),
       has_column_privilege('pantry_grafana','public.custom_commands','name','SELECT'),
       has_column_privilege('pantry_grafana','public.custom_commands','response_template','SELECT'),
       has_column_privilege('pantry_grafana','public.community_actions','user_id','SELECT');
SQL
```

The last verification command must show a login role with all elevated flags
false, exactly 21 direct column SELECT grants matching the table above, and
booleans `f|t|f|f` for table-wide SELECT, required column, and two forbidden
columns. If it cannot be verified, stop before updating the Grafana Secret.
The SQL grant migration is safe to rerun; the
credential update is a rotation and needs coordinated Secret replacement.
Keep both mode-600 temporary credential files until success or rollback is
confirmed. Clean them explicitly at the end.

## Switch Home Grafana

Apply the #412 HBA candidate containing both Home rules and verify a fresh
Grafana connection while the old credential still works. Then, from the same
shell (with `secret_file` and `old_secret_file` still present):

```sh
# Server-side apply does not create a last-applied annotation, but it also
# does not delete an annotation left by older client-side applies.
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability create secret generic grafana-postgres-pantry --from-file=password=/dev/stdin --dry-run=client -o yaml | sudo -n k3s kubectl apply --server-side --force-conflicts -f -' \
  < "$secret_file" > /dev/null
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability annotate secret grafana-postgres-pantry kubectl.kubernetes.io/last-applied-configuration- >/dev/null'
# Check only for key presence. Do not display the annotation or Secret data.
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability get secret grafana-postgres-pantry -o json' \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); assert "kubectl.kubernetes.io/last-applied-configuration" not in s.get("metadata",{}).get("annotations",{})'

scp -o BatchMode=yes observability/grafana-provisioning.yaml \
  minecraftmachine:/tmp/pantry-grafana-provisioning-414.yaml
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability apply -f /tmp/pantry-grafana-provisioning-414.yaml && sudo -n k3s kubectl -n observability rollout restart deployment/grafana && sudo -n k3s kubectl -n observability rollout status deployment/grafana --timeout=180s'
ssh -o BatchMode=yes minecraftmachine \
  'rm -f /tmp/pantry-grafana-provisioning-414.yaml'
```

The committed datasource config must say `user: pantry_grafana`. The
`grafana.yaml` Deployment continues reading password from the same Secret key;
Grafana needs the restart because Kubernetes environment variables do not
update in a running pod.
If the annotation removal or absence assertion fails, stop before the
Grafana restart. The Secret may still carry an older encoded superuser
credential in metadata even when its `password` data has been replaced.

## Post-switch gates

Have the QA agent query the datasource from Grafana, not just PostgreSQL:

1. Datasource health returns OK. A SQL query through the datasource returns
   `current_user = pantry_grafana`, `inet_client_addr() = 100.84.89.87`, and
   `current_database() = pantry`.
2. Execute **all 13 SQL targets** in the checked-in `pantry-bot.json` and the
   **live** `pantry-bot-usage.json`: connection test, events last hour,
   dead-letter count, participant count, events over time, chat actions over
   time, engagement message counts, usage events by source/type, community
   actions over time, command inventory, unused enabled commands, successful
   chat actions in seven days, and usage events last hour. Check rendered
   panels for database errors, including a zero-result period. Also inspect
   all live ConfigMap dashboard SQL for new tables/columns before changing
   credentials; a new target needs an explicit grant review.
3. In a session as `pantry_grafana`, confirm required column `SELECT` works
   while `custom_commands.response_template`, `community_actions.user_id`,
   `engagement_actions.user_id`, and `pantry_events.payload` cannot be read.
   Confirm `INSERT` into `pantry_events`, `CREATE TABLE` in `public`, and a
   superuser-only operation fail. Use a transaction with rollback for the
   write attempt. Confirm no other application table can be selected.
4. Confirm Oracle application pods and fresh backup/ANALYZE Jobs retain their
   connections as `pantry` through the pod-CIDR rule.

Once these pass, remove the Home `pantry` HBA rule and reload. Run the
datasource/panel gates again to show Grafana no longer depends on the
superuser path. Do not rotate `pantry` in this procedure: Oracle application
pods and maintenance Jobs share it; a rotation needs a separate, coordinated
inventory and rollout.

## Rollback

If Grafana fails, re-allow the Home `pantry` HBA line if already removed,
using #412's backed-up HBA and `pg_reload_conf()` procedure. Restore the
previous `user: pantry` datasource ConfigMap from the pre-change checkout,
restore `old_secret_file` through this command, and restart Grafana:

```sh
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl apply --server-side --force-conflicts -f -' \
  < "$old_secret_file" > /dev/null
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability annotate secret grafana-postgres-pantry kubectl.kubernetes.io/last-applied-configuration- >/dev/null'
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability get secret grafana-postgres-pantry -o json' \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); assert "kubectl.kubernetes.io/last-applied-configuration" not in s.get("metadata",{}).get("annotations",{})'
# Reapply the pre-change grafana-provisioning.yaml via MinecraftMachine,
# then restart the deployment as in the switch step.
```

Verify datasource health and all SQL panels. The
new role may remain but can be disabled with `ALTER ROLE pantry_grafana
NOLOGIN` after confirming no session uses it. Delete the temporary credential
files only after the successful or rolled-back state is verified:
`rm -f "$secret_file" "$old_secret_file"`.
