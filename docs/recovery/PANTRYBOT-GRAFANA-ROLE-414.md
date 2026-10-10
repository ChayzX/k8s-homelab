# PantryBot Grafana database role (#414)

Run the role and Grafana credential switch after a current backup has passed
the #412 restore check. The #412 HBA restriction has a separate client
inventory gate and can follow later. Home Grafana remains the monitoring
client, and PantryBot runtime remains on Oracle.

## Observed state and required order

On 2026-10-10 the `PantryPostgres` datasource connected from
`100.84.89.87/32` as `pantry`, which is a database superuser. The checked-in
`pantry-bot.json` has one PostgreSQL SQL target (`SELECT 1`),
while the current live Home health dashboard has none until the dashboard
ConfigMap is deployed. Both the checked-in and live `pantry-bot-usage.json`
have the same eight SQL targets. Those eight queries need five `public` tables:

| Table | Columns required by live SQL |
| --- | --- |
| `pantry_events` | `created_at`, `source`, `event_type` |
| `engagement_actions` | `at`, `source`, `reason`, `action` |
| `custom_commands` | `name`, `enabled`, `category`, `permission_level`, `source`, `use_count`, `created_at` |
| `engagement_message_counts` | `day`, `status`, `count` |
| `community_actions` | `at`, `action` |

All five are ordinary tables without row security. The role gets column
SELECT grants only. It does **not** get `custom_commands.response_template`,
`community_actions.user_id`, `engagement_actions.user_id`, or event payloads.
There are no future-table default grants; review privileges whenever a panel
adds a table or column. Before deployment, confirm the live Grafana ConfigMap
has not gained another SQL dashboard or query since this inventory.
The usage dashboard is now checked in; confirm the generated dashboard
ConfigMap still includes it before any later full dashboard rollout. The live
dashboard ConfigMap has eight additional keys outside this repository's
generated ConfigMap. Do not run the full `grafana-deploy.yml` for #414: its
server-side dashboard ConfigMap apply could remove those live dashboards.
The credential switch below applies only the provisioning ConfigMaps and
restarts Grafana, leaving the dashboard ConfigMap untouched.

The current live HBA still has broad `host all all all scram-sha-256` access,
so the new password-protected role can connect before #412 changes the HBA.
Verify that current rule and the Home source address before starting; do not
apply the #412 candidate just to perform this migration. That candidate
already includes this future Home Grafana rule:

```text
host    pantry          pantry_grafana  100.84.89.87/32         scram-sha-256
```

After the new datasource passes every check, #412 can remove the candidate's
temporary Home `pantry` rule before its own reviewed HBA rollout. It must
retain the Oracle pod-CIDR `pantry` rule for the application and CronJobs.
Until #412's separate inventory gate passes and its HBA is installed, the
old `pantry` superuser credential still authenticates wherever the current
broad HBA and network permit. #414 removes Grafana's use of that credential;
it does not narrow database network access.

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
       has_column_privilege('pantry_grafana','public.community_actions','user_id','SELECT'),
       has_any_column_privilege('pantry_grafana','public.pantry_outbox','SELECT'),
       has_any_column_privilege('pantry_grafana','public.community_participants','SELECT');
SQL
```

The last verification command must show a login role with all elevated flags
false, exactly 19 direct column SELECT grants matching the table above, and
booleans `f|t|f|f|f|f` for table-wide SELECT, required column, two forbidden
columns, and two unused tables. If it cannot be verified, stop before updating
the Grafana Secret.
The SQL grant migration is safe to rerun; the
credential update is a rotation and needs coordinated Secret replacement.
Keep both mode-600 temporary credential files until success or rollback is
confirmed. Clean them explicitly at the end.

## Switch Home Grafana

Confirm the existing Grafana datasource is healthy while its old credential
still works. Confirm the live HBA has not changed since preflight. Then, from
the same shell (with `secret_file` and `old_secret_file` still present):

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
2. Execute all eight current live usage SQL targets and, after deploying the
   checked-in dashboard ConfigMap, the ninth health `SELECT 1` target:
   usage events last hour,
   successful chat actions in seven days, unused enabled commands, command
   inventory, chat actions over time, usage events by source/type, engagement
   message counts, and community actions over time. Check rendered panels for
   database errors, including a zero-result period. Also inspect
   all live ConfigMap dashboard SQL for new tables/columns before changing
   credentials; a new target needs an explicit grant review.
3. In a session as `pantry_grafana`, confirm required column `SELECT` works
   while `custom_commands.response_template`, `community_actions.user_id`,
   `engagement_actions.user_id`, and `pantry_events.payload` cannot be read.
   Confirm `INSERT` into `pantry_events`, `CREATE TABLE` in `public`, and a
   superuser-only operation fail. Use a transaction with rollback for the
   write attempt. Confirm no other application table can be selected.
4. Confirm Oracle application pods and fresh backup/ANALYZE Jobs retain their
   connections as `pantry` through the current HBA. When #412 later installs
   its candidate, verify them again through its pod-CIDR rule.

Once these pass, leave the live HBA unchanged. The separate #412 procedure
will restrict it only after its client inventory gate, then rerun the
datasource and panel checks. Do not rotate `pantry` in this procedure: Oracle
application pods and maintenance Jobs share it; a rotation needs a separate,
coordinated inventory and rollout.

## Rollback

If Grafana fails, restore the previous `user: pantry` datasource ConfigMap
from the pre-change checkout. This procedure leaves the live HBA unchanged;
if an operator also changed it, follow #412's HBA rollback first. Then
restore `old_secret_file` through this command and restart Grafana:

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
