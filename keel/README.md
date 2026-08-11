# `keel` namespace

**Status: applied and live (cut over 2026-08-11).** `kubectl -n keel get pods`
shows `1/1 Running`, zero errors. The sections below describe the deploy
procedure and design rationale for reference/re-deploy — not a pending TODO.

Deploys Keel, the automated updater that preserves pantry-bot's existing
CI → GHCR → auto-redeploy-in-~5min pipeline (today handled by Watchtower with
`--interval 300 --label-enable`, in
`/home/chase/Downloads/pantry-bot/docker-compose.prod.yml`).

Keel is **cluster-scoped by design**: it watches Deployments across namespaces
by their `keel.sh/*` annotations, not by living in the same namespace as what
it manages. Today it watches exactly one Deployment —
`pantry-bot` in the `pantry-bot` namespace (see
`../pantry-bot/40-deployment.yaml` for the three `keel.sh/*` annotations that
put it under Keel's management).

**jmusicbot is deliberately NOT Keel-managed.** It has its own rebuild
pipeline (`auto-update.sh`, cron). Do not add `keel.sh/*` annotations to it —
see `../jmusicbot/README.md`.

---

## Apply order

```bash
# 0. prerequisite — namespaces exist (also creates the `pantry-bot` and
#    `jmusicbot` namespaces this doesn't touch, but Keel's own namespace comes
#    from here too)
kubectl apply -f ../_bootstrap/00-namespaces.yaml

# 1. secret (imperative, never in git) — see SECRETS.md
#    creates: keel-registry-creds
#    ALSO REQUIRES pantry-bot's ghcr-pull-secret to already exist — see
#    ../pantry-bot/SECRETS.md — Keel and the kubelet need separate credentials
#    for the same private image.

# 2. identity + RBAC
kubectl apply -f 10-serviceaccount.yaml
kubectl apply -f 20-clusterrole-clusterrolebinding.yaml

# 3. workload
kubectl apply -f 30-deployment.yaml
```

Apply this namespace **after** `../pantry-bot/` — Keel's ClusterRole grants
permission cluster-wide regardless of order, but there is nothing useful for
Keel to watch/poll until `pantry-bot`'s Deployment (with its `keel.sh/*`
annotations) already exists.

---

## Manual steps — these are not optional

### A. Read `SECRETS.md` in full before applying `30-deployment.yaml`

This is the single most under-documented failure mode in the whole migration:
Keel needs its OWN GHCR credentials to poll the registry API, completely
separate from the `imagePullSecrets` the kubelet uses to actually pull
`pantry-bot`'s image. Skipping this does not produce an error — it produces a
Deployment that just quietly stops receiving updates. See SECRETS.md for the
exact mechanism (verified against the keel-hq/keel source, not assumed) and
the end-to-end test to run before trusting it unattended.

### B. Confirm the image pin, and expect to re-pin it later

`30-deployment.yaml` runs `ghcr.io/keel-hq/keel` pinned by digest to a `master`
build newer than the `0.21.1` release, because the fix for polling *mutable*
tags (`pantry-bot:latest` is exactly that) landed on `master` after `0.21.1`
shipped. Full reasoning and the re-verification commands are in the header
comment of `30-deployment.yaml`. This pin **will go stale** — there is no
auto-update path for Keel itself in this repo (deliberately: an
auto-updating updater is one incident away from updating itself into a broken
state with nothing left to fix it). Revisit every few months.

### C. Give Keel actual work only after verifying it doesn't misfire

Before the first real cutover, watch Keel's logs for a poll cycle against
`pantry-bot` and confirm it does NOT redeploy anything unexpectedly:

```bash
kubectl -n keel logs -f deploy/keel
```

`keel.sh/policy: force` means Keel redeploys on ANY digest change, with no
approval step. That's the desired behaviour (matches Watchtower's `--cleanup`,
fully unattended today), but it also means a bad push to `pantry-bot`'s `main`
branch reaches production in ~5 minutes with nothing in between. That trade-off
already exists today via Watchtower; Keel does not make it worse, but does not
make it safer either.

---

## Verification

1. `kubectl -n keel get pods` → `Running`, not restarting.
2. `kubectl -n keel logs deploy/keel | grep -i pantry` → evidence it's tracking
   the pantry-bot image, no 401/403.
3. **The test that actually matters**: push a trivial commit to pantry-bot's
   `main`, wait for CI to publish, and confirm the pod in `pantry-bot`
   recreates within roughly 5-10 minutes:
   ```bash
   kubectl -n pantry-bot get pods -w
   ```
   Per the migration plan: do this, and see it work, before trusting Keel
   unattended. A pod that's merely `Running` proves nothing about whether the
   pipeline is actually wired up end-to-end.

---

## Design notes / alternatives considered

**Why a ClusterRole/ClusterRoleBinding instead of a Role scoped to
`pantry-bot`'s namespace.** Keel's watch mechanism is inherently cluster-wide —
it discovers annotated Deployments by watching the Deployments API across all
namespaces it can see, not by being told a namespace list. A namespaced Role
would need to be duplicated (and kept in sync) in every namespace that might
ever host a Keel-managed app; today that's only `pantry-bot`, but the whole
point of choosing Keel here was to make that generalizable later without
touching RBAC again.

**Why the ClusterRole is narrower than keel-hq/keel's own Helm chart default.**
The chart's default (`chart/keel/values.yaml`) additionally grants access to
StatefulSets, DaemonSets, Jobs, CronJobs, ReplicationControllers, and `delete`
on pods/replicasets. Nothing in this home-lab uses any of those resource kinds
under Keel, and `delete` in particular is more than "update an image
reference" needs. Note this assumption was wrong for the *watch* path: live
testing (2026-08-11) showed Keel's provider layer watches StatefulSets,
DaemonSets, and CronJobs unconditionally at startup regardless of whether
anything is actually under `keel.sh/policy` — omitting RBAC for them doesn't
silently no-op, it spams `reflector.go` "Unhandled Error"/"forbidden" every
few seconds for the pod's lifetime. Read-only (`get,list,watch`) grants for
those three kinds were added to `20-clusterrole-clusterrolebinding.yaml` to
stop the log spam, with no `update`/`patch` since nothing here is
Keel-managed under those kinds today. If a future workload needs Keel to
*manage* one of those kinds, extend the `update`/`patch` verbs at that point.

**Why `strategy: Recreate` on Keel's own Deployment**, even though Keel itself
is stateless (its sqlite state lives in an `emptyDir`, intentionally
disposable). Consistency with the rest of this repo's pattern, and it avoids a
brief window with two Keel pods both polling and potentially both acting on the
same digest change — cosmetic today (idempotent patches), but not a behaviour
worth relying on.

**Why no Service/Ingress for Keel's UI (port 9300).** Nothing in the migration
plan asked for the dashboard to be reachable, and exposing it means another
auth/access decision this repo doesn't have an opinion on yet. Reach it with
`kubectl -n keel port-forward deploy/keel 9300:9300` when needed.
