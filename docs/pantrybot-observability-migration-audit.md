# Observability Audit: PantryBot Consolidation to Oracle (#320)

**Target State**: PantryBot consolidated exclusively on Oracle (`site: oracle`). Home standby PostgreSQL, Canada node, witness relay, HA fencing scripts, authority promoter, and epoch metrics are decommissioned. Grafana, Authentik, and Home Prometheus/Loki remain on Home (`minecraftmachine`).

---

## 1. Executive Summary & Impact Matrix

| Component | Affected Files | Failure Mode | Severity |
| :--- | :--- | :--- | :--- |
| **Grafana Postgres Panels** | `dashboards/pantry-bot.json`, `scripts/gen-dashboards.py`, `observability/grafana-provisioning.yaml` | `PantryPostgres` datasource targets decommissioned Home standby service `pantry-postgres-local-read`. Panels show connection errors. | **BREAKS** |
| **Grafana HA & Failover Panels** | `dashboards/site-service-health.json`, `dashboards/pantry-bot.json` | Queries for authority epoch, witness lease, failover role, and Canada replication return `No data` or stale metrics. | **BREAKS / STALE** |
| **Home Prometheus Scrape Config** | `observability/prometheus-config.yaml` | Scrapes dead Canada endpoints (`192.168.40.208:13101`) and witness relay. Fails CI contract test. | **BREAKS** |
| **Oracle Prometheus Scrape Config** | `observability/oracle-prometheus-config.yaml` | Scrapes decommissioned witness relay target (`failover-witness-relay...:18765`). | **BREAKS** |
| **CI Test Suite** | `tests/pantrybot-observability-config-test.sh`, `tests/pantrybot-canada-last-resort-guard-test.sh` | Contract tests checking Canada/witness fail or enforce retired HA architecture. | **BREAKS** |
| **Grafana Instance Regex** | `dashboards/pantry-bot.json`, `dashboards/services.json` | Job regex `pantry-bot-(api|gateway|worker|dispatcher)(-oracle|-canada)?` contains obsolete `-canada`. | **COSMETIC** |
| **Loki Log Filters** | `dashboards/logs.json`, `dashboards/pantry-bot.json` | Stream selector `{instance=~"pantry-bot/.*"}` vs `{site="oracle", namespace="pantry-bot"}`. | **COSMETIC** |
| **Silent Secrets Landmine** | `pantry-bot/SECRETS.md`, `pantry-bot/40-deployment.yaml.retired` | Optional secrets `pantry-bot-discord-alerts` and `pantry-bot-authentik-webhook` do not exist in any namespace; failures are silent. | **SILENT OMISSION** |

---

## 2. Grafana Dashboards & Datasources

### 2.1 `dashboards/pantry-bot.json` & `scripts/gen-dashboards.py`

#### A. Database Panels (BREAKS)
- **Files**:
  - `dashboards/pantry-bot.json` (lines 53–75, 178–285)
  - `scripts/gen-dashboards.py` (lines 350–430)
  - `observability/grafana-provisioning.yaml` (lines 17–25)
- **Exact Queries**:
  - Panel 4: `SELECT 1 AS reachable` (Datasource: `PantryPostgres` / uid `pantry-postgres`)
  - Panel 11: `SELECT pg_size_pretty(pg_database_size('pantry_bot')) AS "database_size"`
  - Panel 12: `SELECT count(*) AS total_claims, count(*) FILTER (WHERE claimed_at > NOW() - INTERVAL '24 HOURS') AS claims_last_24h, count(*) FILTER (WHERE claimed_at > NOW() - INTERVAL '1 HOUR') AS claims_last_hour FROM claims`
  - Panel 13: `SELECT max(claimed_at) AS last_claim_at, EXTRACT(EPOCH FROM (NOW() - max(claimed_at))) AS seconds_since_last_claim FROM claims`
  - Panel 14: `SELECT state, count(*) AS count FROM claims GROUP BY state ORDER BY count DESC`
- **Failure Mechanism**: `observability/grafana-provisioning.yaml` configures `PantryPostgres` to connect to `url: pantry-postgres-local-read.pantry-bot.svc.cluster.local:5432` on Home. This service selects `app.kubernetes.io/name: pantry-postgres-authority-standby-home-canada`. When the Home standby database is deleted, the service has 0 endpoints and all 5 SQL panels throw `connection refused` or `no route to host`.
- **Required Fix**:
  1. If Postgres telemetry is retained in Grafana: Update `observability/grafana-provisioning.yaml` to point `PantryPostgres` to the Oracle Postgres endpoint across Tailscale (`100.78.181.15:<nodeport>` or WireGuard tunnel) with read-only credentials.
  2. If direct SQL datasource access from Home to Oracle is deprecated: Remove Panels 4, 11, 12, 13, 14 from `dashboards/pantry-bot.json` and `scripts/gen-dashboards.py`, and rely on Prometheus application metrics (`pantry_bot_runtime_claims_total`, etc.).

#### B. Failover Role Panel (BREAKS / STALE)
- **Files**: `dashboards/pantry-bot.json` (lines 300–335), `scripts/gen-dashboards.py`
- **Exact Query**:
  ```promql
  max by (site) (up{job=~"pantry-bot-api(-oracle|-canada)?"}) == 1
  ```
- **Failure Mechanism**: Panel assumes dynamic failover between multiple sites (`home`, `oracle`, `canada`). Once consolidated, Oracle is the sole site.
- **Required Fix**: Update query to `max by (site) (up{job=~"pantry-bot-api.*"})` or replace the failover role panel with an Oracle service health indicator.

#### C. Active Instances & Replicas (COSMETIC)
- **Files**: `dashboards/pantry-bot.json` (lines 20–45)
- **Exact Query**:
  ```promql
  count(up{job=~"pantry-bot-(api|gateway|worker|dispatcher)(-oracle|-canada)?"} == 1) or vector(0)
  ```
- **Failure Mechanism**: The `-canada` branch is dead. Query continues to match `-oracle` jobs, but contains obsolete legacy regex patterns.
- **Required Fix**: Simplify regex to `pantry-bot-(api|gateway|worker|dispatcher)(-oracle)?`.

---

### 2.2 `dashboards/site-service-health.json`

#### A. Failover State & Authority Epoch (BREAKS)
- **Files**: `dashboards/site-service-health.json` (lines 40–110)
- **Exact Queries**:
  - `pantry_bot_postgres_authority_epoch`
  - `failover_witness_lease_state`
  - `failover_witness_authority_epoch`
- **Failure Mechanism**: The failover witness relay and epoch tracking systems are being removed. These panels will permanently report `No data` or red error states.
- **Required Fix**: Delete HA/epoch panels from `dashboards/site-service-health.json`.

#### B. Canada Standby Health & Replication Lag (BREAKS / STALE)
- **Files**: `dashboards/site-service-health.json` (lines 120–180)
- **Exact Queries**:
  - `pg_stat_replication_lag{instance=~".*canada.*"}`
  - `up{job="pantry-postgres-canada"}`
- **Failure Mechanism**: Canada infrastructure is fully decommissioned. Panels will display broken / absent metric indicators.
- **Required Fix**: Remove Canada-specific rows and panels from `dashboards/site-service-health.json`.

---

## 3. Prometheus Scraping & Remote Write

### 3.1 Home Cluster (`observability/prometheus-config.yaml`)

#### A. Obsolete Canada & Witness Targets (BREAKS CI & SCRAPES)
- **File**: `observability/prometheus-config.yaml`
- **Exact Lines**:
  ```yaml
  # Lines 145-168 (Witness scrape jobs)
  - job_name: pantry-postgres-witness
    static_configs:
      - targets: ['failover-witness-relay.observability.svc.cluster.local:18765']
        labels:
          site: home

  # Lines 180-210 (Canada targets)
  - job_name: pantry-postgres-canada
    static_configs:
      - targets: ['192.168.40.208:13101']
        labels:
          site: canada
  ```
- **Failure Mechanism**:
  1. Home Prometheus wastes scrape cycles attempting to reach non-existent Canada IP `192.168.40.208` and witness relay `18765`, logging recurring context deadline errors.
  2. `tests/pantrybot-observability-config-test.sh` fails immediately upon detecting `site: canada` references in config.
- **Required Fix**:
  - Remove all `pantry-postgres-witness` and `site: canada` scrape jobs from `observability/prometheus-config.yaml`.
  - Remove Home local `pantry-bot-*` scrape jobs once Home workloads are removed.

---

### 3.2 Oracle Cluster (`observability/oracle-prometheus-config.yaml`)

#### A. Oracle Witness Target (BREAKS SCRAPE)
- **File**: `observability/oracle-prometheus-config.yaml`
- **Exact Lines**:
  ```yaml
  - job_name: pantry-postgres-witness-oracle
    metrics_path: /metrics
    static_configs:
      - targets: ['failover-witness-relay.observability.svc.cluster.local:18765']
        labels:
          site: oracle
  ```
- **Failure Mechanism**: Witness relay target on Oracle fails to resolve / scrape once failover witness containers are stopped.
- **Required Fix**: Remove `pantry-postgres-witness-oracle` scrape job from `observability/oracle-prometheus-config.yaml`.

#### B. Oracle Workload Scrape Verification (HEALTHY)
- **File**: `observability/oracle-prometheus-config.yaml`
- **Scrape Jobs Confirmed**:
  - `pantry-bot-api-oracle` -> `pantry-private-api.pantry-bot.svc.cluster.local:8000`
  - `pantry-bot-gateway-oracle` -> `pantry-twitch-gateway.pantry-bot.svc.cluster.local:8080`
  - `pantry-bot-worker-oracle` -> `pantry-chat-worker.pantry-bot.svc.cluster.local:8081`
  - `pantry-bot-dispatcher-oracle` -> `pantry-twitch-dispatcher.pantry-bot.svc.cluster.local:8082`
  - `remote_write` -> `http://100.115.92.64:30090/api/v1/write` (Home write gateway)
- **Status**: Operational. Remote write correctly ships all `site="oracle"` series to Home Prometheus.

---

## 4. Loki & Alloy Logging Pipeline

### 4.1 Log Flow Configuration (`observability/alloy-logs-oracle.yaml`)
- **Status**: **OPERATIONAL**
- **Architecture**:
  - Oracle Alloy runs `loki.source.kubernetes` collecting container logs across all namespaces (including `pantry-bot`).
  - Labeled with `site: "oracle"`, `cluster: "oracle"`.
  - Remote-written via NodePort to Home Loki: `http://100.115.92.64:31100/loki/api/v1/push`.
- **Audit Finding**: When Home `pantry-bot` workloads are scaled down/removed, all logs for `namespace="pantry-bot"` will originate from `site="oracle"`.

### 4.2 Log Dashboard Selectors (`dashboards/logs.json`, `dashboards/pantry-bot.json`)
- **File**: `dashboards/logs.json` (lines 45–90) & `dashboards/pantry-bot.json` (lines 280–310)
- **Exact Query**:
  ```logql
  {instance=~"pantry-bot/.*", level=~"error|warn"}
  ```
- **Failure Mode**: COSMETIC.
- **Analysis**: LogQL queries targeting `{namespace="pantry-bot"}` will seamlessly display Oracle logs. If queries specifically filter `{site="home"}`, they will return empty streams once Home workloads terminate.
- **Required Fix**: Ensure log panels in `pantry-bot.json` and `logs.json` query `{namespace="pantry-bot"}` without hardcoded `{site="home"}` constraints.

---

## 5. Alert Evaluators & Monitor Scripts

### 5.1 `observability/k3s-watcher/watcher.py`
- **File**: `observability/k3s-watcher/watcher.py` (lines 60–120)
- **Monitored Namespaces**: `['default', 'observability', 'authentik', 'pantry-bot', 'jmusicbot', 'opsbot', 'minecraft']`
- **Behavior on Home**:
  - Watches local pods via Kubernetes API.
  - If Home `pantry-bot` deployments are removed or scaled to 0 replicas, `watcher.py` correctly reports 0 pod errors (clean state).
  - Note: `FUNCTIONAL_HEALTH_URLS` in `watcher.py` checks public endpoints (`commands.greeniespantry.uk`, `app.greeniespantry.uk`), which route through Cloudflare to Oracle. No code changes required.

### 5.2 `observability/external-monitor/monitor.py`
- **File**: `observability/external-monitor/monitor.py` (lines 40–85)
- **Endpoints Checked**:
  - `https://commands.greeniespantry.uk/healthz`
  - `https://app.greeniespantry.uk/healthz`
- **Status**: **OPERATIONAL**. Both hostnames have been routed to Oracle since 2026-09-15. External probe will continue validating Oracle runtime health without modification.

---

## 6. Secret Hygiene & Silent Landmines

### 6.1 Unset Optional Secrets
- **Files**:
  - `pantry-bot/40-deployment.yaml.retired` (lines 185–205)
  - `pantry-bot/SECRETS.md` (lines 170–185)
- **Secrets**:
  - `pantry-bot-discord-alerts` (Key: `DISCORD_WEBHOOK_URL`)
  - `pantry-bot-authentik-webhook` (Key: `AUTHENTIK_WEBHOOK_SECRET`)
- **Current Cluster State**:
  - Checked `pantry-bot` namespace on Home: **Missing**
  - Checked `pantry-bot` namespace on Oracle: **Missing**
- **Risk**:
  - Pod specs define these secrets with `optional: true`.
  - Kubernetes creates the pods without warnings.
  - Runtime alerting inside PantryBot components fails silently with no Kubernetes events generated.
- **Required Fix**:
  - If Discord operational alerting is desired directly from PantryBot: Create secret `pantry-bot-discord-alerts` in `pantry-bot` on Oracle.
  - If alerting is handled by `k3s-watcher` and Prometheus Alertmanager: Remove the unused optional env bindings from the active deployment manifests to eliminate silent drift.

---

## 7. Step-by-Step Remediation Plan

1. **Update Prometheus Configurations**:
   - `observability/prometheus-config.yaml`: Strip Canada targets (`192.168.40.208`) and witness relay jobs.
   - `observability/oracle-prometheus-config.yaml`: Strip witness relay job `pantry-postgres-witness-oracle`.

2. **Update Grafana Dashboards & Generator**:
   - Edit `scripts/gen-dashboards.py` to remove dead Postgres queries (or re-point `PantryPostgres` datasource to Oracle over Tailscale).
   - Edit `dashboards/site-service-health.json` to delete Canada replication and HA witness epoch panels.
   - Run `python3 scripts/gen-dashboards.py` and `python3 scripts/gen-dashboards-configmap.py`.
   - Validate with `bash scripts/validate-grafana-dashboards.sh`.

3. **Update CI Contracts**:
   - Update `tests/pantrybot-observability-config-test.sh` to remove witness assertions.
   - Retire or adapt `tests/pantrybot-canada-last-resort-guard-test.sh` and `tests/pantrybot-home-return-standby-test.sh`.

4. **Decommission Witness & Standby Resources**:
   - Retire systemd services in `observability/failover-witness/`.
   - Remove Home service `pantry-postgres-local-read.yaml`.
