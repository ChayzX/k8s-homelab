# PantryBot Grafana database role (#414)

Run this during the coordinated #412 Oracle PostgreSQL access window. Home
Grafana remains the monitoring client. This change does not migrate PantryBot
runtime away from Oracle.

## Observed state and required order

On 2026-10-10 the `PantryPostgres` datasource connected from
`100.84.89.87/32` as `pantry`, which is a database superuser. The dashboard's
SQL panels read only `public.pantry_events`, `public.pantry_outbox`, and
`public.community_participants`. All three tables belong to `pantry`; row
security is disabled. The new role has SELECT only on those three tables.
There are no future-table default grants; review privileges whenever a panel
adds a table.

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
set -eu
umask 077
secret_file=$(mktemp)
old_secret_file=$(mktemp)
openssl rand -hex 32 | tr -d '\n' > "$secret_file"

# Preserve the existing Home Secret for immediate rollback. Treat this file
# as a credential even though its data field is base64 encoded.
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability get secret grafana-postgres-pantry -o json' \
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
SELECT has_table_privilege('pantry_grafana','public.pantry_events','SELECT'),
       has_table_privilege('pantry_grafana','public.pantry_outbox','SELECT'),
       has_table_privilege('pantry_grafana','public.community_participants','SELECT');
SQL
```

The last verification command must show a login role with all elevated flags
false and all three SELECT checks true. If it cannot be verified, stop before
updating the Grafana Secret. The SQL grant migration is safe to rerun; the
credential update is a rotation and needs coordinated Secret replacement.
Keep both mode-600 temporary credential files until success or rollback is
confirmed. Clean them explicitly at the end.

## Switch Home Grafana

Apply the #412 HBA candidate containing both Home rules and verify a fresh
Grafana connection while the old credential still works. Then, from the same
shell (with `secret_file` and `old_secret_file` still present):

```sh
# Server-side apply avoids a last-applied annotation containing Secret data.
ssh -o BatchMode=yes minecraftmachine \
  'sudo -n k3s kubectl -n observability create secret generic grafana-postgres-pantry --from-file=password=/dev/stdin --dry-run=client -o yaml | sudo -n k3s kubectl apply --server-side --force-conflicts -f -' \
  < "$secret_file" > /dev/null

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

## Post-switch gates

Have the QA agent query the datasource from Grafana, not just PostgreSQL:

1. Datasource health returns OK. A SQL query through the datasource returns
   `current_user = pantry_grafana`, `inet_client_addr() = 100.84.89.87`, and
   `current_database() = pantry`.
2. Execute every SQL panel in `dashboards/pantry-bot.json`: connection test,
   events last hour, dead-letter count, participant count, and events over
   time. Check the rendered panel for database errors, including a zero-result
   period.
3. In a session as `pantry_grafana`, confirm `SELECT` works on the three
   tables, while `INSERT` into `pantry_events`, `CREATE TABLE` in `public`,
   and a superuser-only operation fail. Use a transaction with rollback for
   the write attempt. Confirm no other application table can be selected.
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
# Reapply the pre-change grafana-provisioning.yaml via MinecraftMachine,
# then restart the deployment as in the switch step.
```

Verify datasource health and all SQL panels. The
new role may remain but can be disabled with `ALTER ROLE pantry_grafana
NOLOGIN` after confirming no session uses it. Delete the temporary credential
files only after the successful or rolled-back state is verified:
`rm -f "$secret_file" "$old_secret_file"`.
