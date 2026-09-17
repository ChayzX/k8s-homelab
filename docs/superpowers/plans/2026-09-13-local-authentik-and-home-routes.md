# Local Authentik and Home-Preferred Interactive Routes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove normal Frankfurt round trips from Authentik/Grafana access while retaining fenced Oracle promotion capacity.

**Architecture:** Home Authentik and PostgreSQL are the normal local writer and interactive origin. Oracle remains standby capacity and is made public only after controlled promotion. Cloudflare route ownership is documented and verified separately from PantryBot.

**Tech Stack:** Kubernetes manifests, Authentik 2026.5.6, PostgreSQL streaming replication, Cloudflare Tunnel, Bash contract tests, GitHub Issues.

**Spec:** `docs/superpowers/specs/2026-09-13-local-authentik-and-home-routes-design.md`

## Global Constraints

- Do not modify PantryBot deployments, database, leases, queues, or Cloudflare PantryBot tunnels.
- Do not expose secret values; inspect only key names and non-sensitive host/port values.
- Never run two writable Authentik PostgreSQL authorities.
- Do not enable Oracle public Authentik routes while Oracle PostgreSQL is a standby.
- Preserve unrelated dirty worktree changes.
- Every live change must have a read-only precheck and a rollback command.

### Task 1: Add regression contracts for locality and fencing

**Files:**
- Create: `tests/authentik-locality-routing-test.sh`
- Modify: `docs/recovery/AUTHENTIK-HA-READINESS.md`
- Modify: `docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md`

**Interfaces:**
- The test consumes rendered Authentik manifests and the routing contract.
- It produces a non-zero exit if Home Authentik points at a remote IP, or if
  the documented normal route does not identify Home as owner.

- [ ] **Step 1: Write the failing contract test**

  Assert that the tracked Home Authentik Secret template uses
  `auth-postgresql.auth.svc.cluster.local`, that Oracle's standby endpoint is
  not used by Home, and that the routing document states Home ownership for
  normal `auth`, `oauth`, `grafana`, and `operations` routes.

- [ ] **Step 2: Run it and verify it fails against the current remote Home host**

  Run: `bash tests/authentik-locality-routing-test.sh`

  Expected: FAIL identifying the remote Home Authentik PostgreSQL endpoint or
  missing Home route ownership.

- [ ] **Step 3: Commit the test and documentation assertions**

  Run: `git add tests/authentik-locality-routing-test.sh docs/recovery/AUTHENTIK-HA-READINESS.md docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md && git commit -m 'test(auth): enforce local primary and home routes'`

### Task 2: Make Home Authentik local-primary in tracked configuration

**Files:**
- Modify: the tracked Home Authentik Secret/config source identified by
  `rg -n 'AUTHENTIK_POSTGRESQL__HOST|auth-authentik' auth k8s`
- Modify: `tests/authentik-manifest-test.sh` if needed to cover the local host

**Interfaces:**
- Authentik consumes the local Home PostgreSQL ClusterIP/DNS Service.
- Existing Oracle standby replication and promotion scripts remain the
  failover interface.

- [ ] **Step 1: Locate the source manifest and render it**

  Run: `rg -n 'AUTHENTIK_POSTGRESQL__HOST|auth-authentik' auth k8s && kubectl kustomize <home-auth-path> >/tmp/auth-home.yaml`

- [ ] **Step 2: Change only the Home database host to the local Service**

  Set `AUTHENTIK_POSTGRESQL__HOST=auth-postgresql.auth.svc.cluster.local` and
  keep the existing database name, port, and credentials references unchanged.

- [ ] **Step 3: Render and run the locality/manifest tests**

  Run: `kubectl kustomize <home-auth-path> >/tmp/auth-home.yaml && bash tests/authentik-manifest-test.sh && bash tests/authentik-locality-routing-test.sh`

  Expected: PASS with no secret values printed.

- [ ] **Step 4: Commit the tracked configuration**

  Run: `git add auth k8s tests/authentik-manifest-test.sh tests/authentik-locality-routing-test.sh && git commit -m 'fix(auth): use local home postgres authority'`

### Task 3: Document and validate Home-owned normal routes

**Files:**
- Modify: `docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md`
- Modify: `docs/recovery/runbooks/authentik.md`
- Create: `tests/interactive-route-ownership-test.sh`

**Interfaces:**
- Cloudflare tunnel configuration is external to Git; the runbook provides the
  exact route ownership and verification commands.
- The test consumes route hostnames and expected origin markers, without
  changing Cloudflare.

- [ ] **Step 1: Add the route ownership contract**

  Record Home as the normal owner for `auth`, `oauth`, `grafana`, and
  `operations`; record Oracle as the promotion owner and state that both
  tunnels must not be treated as equal origins for stateful Authentik.

- [ ] **Step 2: Add a read-only route timing/ownership harness**

  The harness must issue three requests per hostname, record HTTP status,
  time-to-first-byte, and the optional origin marker, and fail only when an
  expected normal origin marker is explicitly supplied and mismatched.

- [ ] **Step 3: Run shell/YAML checks and commit**

  Run: `bash tests/interactive-route-ownership-test.sh` and the existing
  `tests/authentik-readiness-doc-test.sh`.

### Task 4: Roll out Home Authentik locally with rollback

**Files:**
- No repository files; use the approved rendered manifest and documented live
  change.

**Interfaces:**
- Kubernetes Home Authentik Deployment, Secret, and local PostgreSQL Service.
- Cloudflare routes remain unchanged during this task.

- [ ] **Step 1: Capture sanitized precheck**

  Confirm Home PostgreSQL `pg_is_in_recovery()=false`, Oracle Authentik
  PostgreSQL `pg_is_in_recovery()=true`, Home Authentik readiness 200, and all
  PantryBot deployments/leases/queues healthy.

- [ ] **Step 2: Apply only the Home Authentik database-host change**

  Patch the Home Secret from the old remote host to
  `auth-postgresql.auth.svc.cluster.local`, then restart only the Home
  Authentik server and worker using a rolling restart.

- [ ] **Step 3: Verify login path and rollback readiness**

  Check Authentik readiness, OAuth redirect, Grafana redirect, and logs for
  database errors. If any fail, restore the previous Secret value from the
  precheck record and roll back the two Authentik Deployments.

- [ ] **Step 4: Recheck PantryBot without restarting it**

  Verify commands HTTP 200, all PantryBot pods Ready, Oracle lease ownership,
  and zero pending/failed durable queue rows.

### Task 5: Pin Cloudflare normal routes and update failover evidence

**Files:**
- Modify: `docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md`
- Modify: `docs/recovery/runbooks/authentik.md`
- Modify: GitHub issues #198 and #191

**Interfaces:**
- Cloudflare MCP/API or authenticated Cloudflare control plane.
- Normal routes: Authentik, OAuth, Grafana, Operations -> Home origins.
- Promotion routes: Oracle origins enabled only after Oracle DB promotion.

- [ ] **Step 1: Export current route configuration without secrets**

  Record hostname, tunnel/origin identifier, and route order only.

- [ ] **Step 2: Change normal routes to Home-only origins**

  Remove Oracle as a normal equal origin for stateful routes while leaving the
  PantryBot commands route unchanged.

- [ ] **Step 3: Verify external status and timing**

  Check three samples each for Authentik readiness, OAuth login redirect,
  Grafana redirect, and Operations redirect; record p50/p95-style samples and
  expected origin marker where available.

- [ ] **Step 4: Verify Oracle promotion route is disabled while standby**

  Confirm Oracle Authentik is not externally reachable through the normal
  public hostnames while its database reports recovery mode.

- [ ] **Step 5: Attach evidence to issues #198 and #191**

  Include sanitized route ownership, timing, database roles, rollback command,
  and the unchanged PantryBot health evidence.

### Task 6: Final verification and handoff

**Files:**
- Modify: `docs/recovery/AUTHENTIK-HA-READINESS.md` only for evidence links.

- [ ] **Step 1: Run all Authentik and routing contract tests**

  Run: `for t in tests/authentik-*.sh tests/interactive-route-ownership-test.sh; do bash "$t"; done`

- [ ] **Step 2: Run the repository-wide relevant shell tests**

  Run the existing Authentik, routing, Grafana budget, PantryBot split,
  failover, and target-selection tests.

- [ ] **Step 3: Confirm no PantryBot manifests or live workloads changed**

  Compare PantryBot deployment images/generations, leases, queue counts, and
  public commands response with the precheck.

- [ ] **Step 4: Commit evidence and report**

  Commit only documentation/test changes, then link the commit and issue
  comments in the final handoff.
