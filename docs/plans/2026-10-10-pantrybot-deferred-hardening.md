# PantryBot Deferred Hardening Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the deferred PantryBot database and Oracle host hardening while preserving the live app, monitoring, backup, and documented recovery paths.

**Architecture:** Apply changes in independently verifiable gates. PostgreSQL access remains available to Oracle pod CIDR workloads and the database's local Unix socket; Grafana uses its dedicated read-only account from MinecraftMachine. Host network restrictions preserve the tested SSH jump route and Kubernetes scrape path. Rotate credentials only after every credential holder and scheduled client is accounted for.

**Tech Stack:** Kubernetes/k3s, PostgreSQL 16, PostgreSQL HBA, Tailscale, Ubuntu/apt, systemd, node-exporter, Grafana, shell/Python runbooks.

## Global Constraints

- Production is Oracle-only. Do not restore HA, Home PantryBot runtime, or retired replication clients.
- Never print, place in argv, or commit secret values. Do not generate synthetic Twitch/YouTube chat activity.
- Before each production mutation, capture rollback state and verify the operation's recovery route. Preserve SSH via MinecraftMachine until a separate OCI console route is proven.
- Do not narrow a route needed by a live service until its source and a post-change success check are established.
- Avoid the daily PostgreSQL backup at 05:10 UTC and Sunday ANALYZE at 04:23 UTC; do not overlap maintenance jobs.
- Do not remove historical credentials or temporary recovery objects until all live consumers have moved and recovery checks pass.

---

### Task 1: Narrow PostgreSQL HBA to verified Oracle and monitoring clients

**Files:**
- Modify: `pantry-bot/31-postgres-pg-hba.conf`
- Modify: `docs/recovery/PANTRYBOT-POSTGRES-ACCESS-412.md`

**Interfaces:**
- Consumes: Oracle pod CIDR `10.42.0.0/24`, Home Grafana source `100.84.89.87/32`, local socket client, current live `pg_hba_file_rules`.
- Produces: Candidate rules allowing local socket access, role `pantry` from pod CIDR, role `pantry_grafana` from Grafana source; rejecting other TCP and all remote replication.

- [ ] **Step 1: Refresh and review client evidence (partial; external clients remain unverified)**

Run read-only live inventory: `ssh oracle 'sudo k3s kubectl -n pantry-bot exec pantry-postgres-0 -- psql -U pantry -d pantry -AtX -c "SELECT usename,datname,client_addr,state,count(*) FROM pg_stat_activity WHERE datname IS NOT NULL GROUP BY 1,2,3,4 ORDER BY 1,3"'`. Confirm Grafana datasource identity with a live query, enumerate all Deployments/CronJobs/Jobs and direct-access policy, and confirm no active replication clients/slots. Record source classes without secret values.

Expected: all serving DB clients and the backup/ANALYZE Jobs are inside `10.42.0.0/24`; Grafana is `pantry_grafana` from `100.84.89.87/32`; no active replication sessions or slots were found. Local/system schedules were checked. An external password manager or intermittent direct SQL client has not been fully inventoried; such clients are excluded by the applied HBA and are documented as a remaining check.

- [x] **Step 2: Validate the candidate and fresh restore**

Run the tracked restore rehearsal from `docs/recovery/pantrybot-postgres-restore-check-412.yaml` against the newest R2 dump in an isolated namespace. Require successful PostgreSQL 16 restore with a plausible table count/size, then remove the scratch namespace. Parse candidate HBA rules and require zero parse errors. Done before HBA apply, and repeated against the first post-change backup.

- [x] **Step 3: Install atomically with rollback available**

On the PostgreSQL PVC, save current `pg_hba.conf` as `pg_hba.conf.pre-412`, copy the reviewed candidate through a temporary file, set owner `postgres:root` and mode `600`, atomically rename, and check `pg_hba_file_rules` before `SELECT pg_reload_conf()`. Do not restart PostgreSQL. Done; previous file retained and parser reports zero errors.

- [ ] **Step 4: Verify every allowed path and reject an unapproved source (allow paths verified; negative peer probe pending)**

Create fresh DB connections from every DB-using app pod and one new backup and ANALYZE Job. Query Grafana's live datasource as `pantry_grafana`; verify all PantryBot dashboard queries return without datasource errors. Verify local socket operator access. Done: all nine serving pods connected, backup uploaded and verified in R2, ANALYZE completed, Grafana health was OK, and local socket query succeeded. No separate nonallowlisted peer was available for a negative PostgreSQL network probe.

- [x] **Step 5: Roll back immediately on any required-path failure**

Rollback instructions are retained. No rollback was needed because every required connection check passed.

---

### Task 2: Rotate PostgreSQL superuser and retire stale copies

**Files:**
- Review and track: `scripts/pantrybot-secret-update-415.py`
- Review and track: `docs/recovery/pantrybot-password-probe-415.yaml.tpl`
- Update: `docs/recovery/PANTRYBOT-POSTGRES-PASSWORD-ROTATION-415.md`

**Interfaces:**
- Consumes: hidden-prompt helper, two distinct generated credentials, Oracle serving Secret, StatefulSet bootstrap Secret, six DB-consuming Deployments, CronJobs.
- Produces: staged temporary and final connection URLs, verified fresh connections, retired old credential copies removed after use.

- [ ] **Step 1: Review helper and close holder inventory**

Review the helper for secret-safe stdin handling, role/database/host validation, annotation removal, and idempotent cleanup. Recheck all Kubernetes workload references in Oracle and Home, local files, operator vault/password manager, external schedules, and database connection records after enabling `log_connections` for an observation window. Verify the restore and local-socket rollback gate. Do not rotate until every holder has a disposition.

- [ ] **Step 2: Stage and probe a temporary superuser**

Using interactive hidden prompts, snapshot the old URL in a temporary Kubernetes Secret and create `pantry_rotation_415` with a new credential. Probe from the pod CIDR and require `current_user=pantry_rotation_415`, `current_database=pantry`, and `pg_is_in_recovery()=false`.

- [ ] **Step 3: Move consumers sequentially to the temporary credential**

Promote the temporary URL and restart `pantry-private-api`, `pantry-overlay-delivery`, `pantry-chat-worker`, `pantry-maintenance-responder`, `pantry-twitch-dispatcher`, then `pantry-twitch-gateway`, waiting for rollout and a fresh connection from every replica after each deployment. Run one backup and one ANALYZE Job; confirm backup artifact exists in R2. If any fails before changing `pantry`, restore the old URL and repeat the sequential rollout.

- [ ] **Step 4: Rotate `pantry` and move consumers to final credential**

Change `pantry` through the local socket using psql's hidden `\\password` prompt. Keep the old-credential snapshot until `stage-final` and its probe succeed; the helper reads it to reject password reuse. Delete it immediately before promoting the final URL. Sequentially restart the same six deployments, and verify every replica plus backup, ANALYZE, Grafana, and dashboards. If this phase fails, recover through the temporary superuser; never reinstate the old password.

- [ ] **Step 5: Align bootstrap state and delete obsolete copies**

Set the StatefulSet bootstrap `POSTGRES_PASSWORD` to the final password through the hidden prompt without restarting PostgreSQL. Recheck that Home workloads do not reference the stale Pantry platform Secret, then delete that Secret and its annotation snapshot. Remove old helper/probe Jobs and temporary/final URL Secrets only after a monitored stability period and no role sessions remain. Disable login and drop the temporary role only after confirming it owns no objects.

---

### Task 3: Restrict Oracle host services and update packages

**Files:**
- Modify: `oracle-node-exporter-isolation` host firewall persistence/configuration in the `k8s-homelab` repository, after reviewing the existing branch against `origin/main`.
- Update: `docs/recovery` with exact applied routes and rollback commands.

**Interfaces:**
- Consumes: live `iptables`/nftables rules, k3s pod CIDR, Prometheus target `100.78.181.15:9100`, tested SSH route through MinecraftMachine, package candidate/security status.
- Produces: node-exporter reachable by Oracle scraper/pod path and denied to unapproved tailnet peers; updated supported host packages; preserved SSH and k3s access.

- [x] **Step 1: Establish access-control and rollback facts**

Inspected Oracle listeners, routes, active firewall implementation, cloud NSG visibility, package candidates, and the stale exporter branch. Confirmed k3s API is loopback/tailnet-bound, Prometheus pod is `10.42.0.200`, and SSH currently relies on MinecraftMachine. The independent OCI recovery route remains unavailable; no SSH or Tailscale policy change is included.

- [x] **Step 2: Restrict exporter port 9100**

Installed `ops/oracle/pantrybot-node-exporter-guard.nft` as the independent `inet pantrybot_guard` systemd service. MinecraftMachine returned HTTP 200 before the rule and timed out afterward; Prometheus remained `up=1` after a fresh scrape. The rule is independent of kube-router and Tailscale chains.

- [ ] **Step 3: Upgrade selected packages with access checks**

Updated `libnetplan0`, `netplan-generator`, `netplan.io`, and `python3-netplan` to Ubuntu `0.107.1-3ubuntu0.22.04.5`; a new SSH connection succeeded and networkd, Tailscale, and k3s remained active. `tailscale` (`1.104.1` candidate) and held-back `dnsmasq-base` remain pending. Do not modify OCI SSH ingress, tailnet-wide policy, or port 6443 without a proven independent recovery path and confirmed live routes.

---

### Task 4: Close observability QA gaps

**Files:**
- No repository changes unless browser verification identifies a reproducible dashboard defect.

**Interfaces:**
- Consumes: Home Grafana Pantry Usage and Chat Usage dashboards, datasource `PantryPostgres`, live read-only browser/session.
- Produces: visual render evidence and a current QA note; a nonzero reply persistence check only after naturally occurring chat activity.

- [ ] **Step 1: Verify rendered dashboards**

Open both dashboards in the authenticated browser, inspect the rendered panels and time range, and compare visible values with the Grafana query API and live scrape data. Record any visual or query mismatch with panel name and evidence. Do not modify dashboard data or send synthetic chat.

- [ ] **Step 2: Observe real reply persistence**

Wait for naturally generated bot reply activity. Confirm the send/fail counters advance and the reply row persists without message text. If no real reply occurs during observation, report this check as pending rather than generating traffic.

---
