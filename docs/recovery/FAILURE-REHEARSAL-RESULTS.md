# Failure rehearsal results

This record contains only disposable or read-only evidence. No production
route, deployment, Minecraft workload, or persistent database was changed.

## 2026-09-10 — application ownership handoff

- Scope: real disposable PostgreSQL on home/chasebot.
- Result: passed. Home acquired epochs 1 and 3; Oracle was rejected while home
  owned the lease; Oracle acquired epochs 2 and 4 after handoff; the stale
  home token was rejected.
- Cleanup: disposable namespace removed.

## 2026-09-10 — Oracle platform portability

- Scope: real disposable PostgreSQL 16 on Oracle arm64 through an SSH tunnel.
- Result: passed. Duplicate event/outbox protection, claim, completion, and
  terminal status behavior matched the home rehearsal.
- Cleanup: disposable namespace removed.

## 2026-09-10 — home-to-Oracle logical replication

- Scope: disposable PostgreSQL 16 instances with logical replication enabled.
- Result: passed. Oracle reached the home NodePort, created a subscription, and
  observed a row written on home.
- Interpretation: async one-way transport/WAL proof only; not promotion,
  automatic failover, multi-primary, or zero-RPO evidence.
- Cleanup: subscription and both disposable namespaces removed.

## 2026-09-10 — cross-site promotion script hardening

- Scope: disposable PostgreSQL pods and the new `rehearsal:promotion` command.
- Result: no evidence recorded. The first run was stopped after it remained
  pending without a configured subscription; the script was then hardened with
  PostgreSQL statement timeouts. Temporary namespaces were removed.
- Next gate: rerun only after the disposable publication/subscription and
  operator-side promotion steps are explicitly configured.

## 2026-09-10 — cross-site application promotion

- Scope: disposable home and Oracle PostgreSQL instances with `pantry_leases`
  included in the logical publication.
- Result: passed. Home epoch 1 was observed on Oracle; Oracle acquired epoch 2
  after home lease expiry; the stale home fencing token was rejected on Oracle.
- Interpretation: application-side promotion and fencing proof only. The
  rehearsal explicitly left PostgreSQL promotion and old database-writer
  fencing as operator/controller responsibilities.
- Cleanup: replication slot and both disposable namespaces removed.

## 2026-09-10 — Oracle split-UI runtime validation

- Scope: PantryBot candidate images from commit `46fc4f3`, deployed only to the
  standalone Oracle ARM64 k3s namespace; no home production workload changed.
- Result: passed. `pantry-commands-site` reached 2/2 Ready replicas and its
  `/ready` and `/api/public/commands` endpoints returned successfully.
  `pantry-private-site` reached 2/2 Ready replicas and its `/ready` and `/mod/`
  endpoints returned successfully. All four pods were running with zero
  restarts during the check.
- Interpretation: proves multi-architecture packaging, Oracle scheduling, and
  split stateless UI readiness. It does not prove private API/database,
  Twitch ownership, external routing, or PostgreSQL promotion.
- Cleanup: port-forwards were stopped; the two UI Deployments remain as the
  Oracle candidate capacity for the next bootstrap gate.
