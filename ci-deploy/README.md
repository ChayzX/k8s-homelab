# `ci-deploy` — scoped kubeconfig source for self-hosted GitHub Actions runners

Not a namespace of its own — these manifests create ServiceAccounts, Roles,
RoleBindings, and token Secrets **inside** the existing `pantry-bot`, `opsbot`,
and `observability` namespaces (see `../_bootstrap/00-namespaces.yaml`). Nothing here
creates a namespace, and nothing here runs a pod.

Replaces a cluster-admin kubeconfig that was about to be handed to the
self-hosted GitHub Actions runner(s) being set up per epic `k8s-homelab-oiv`
(this task: `k8s-homelab-oiv.2`). That kubeconfig could do anything in the
cluster; a CI runner only needs to run `kubectl set image`,
`kubectl rollout status`, and `kubectl rollout undo` against two Deployments.

## What each file does

| File | Purpose |
|---|---|
| `10-serviceaccount.yaml` | Three `ci-deploy` ServiceAccounts — one in `pantry-bot`, one in `opsbot`, and one in `observability`. |
| `20-rbac.yaml` | A `Role` + `RoleBinding` per namespace. App namespaces get deployment mutation and read-only pod access; observability additionally gets only the dashboard ConfigMap write needed by Grafana deploys. |
| `30-token-secret.yaml` | A durable token `Secret` per ServiceAccount (SAs stopped auto-creating these in Kubernetes 1.24+). |

## Apply order

```bash
# 0. prerequisite — pantry-bot, opsbot, and observability namespaces already exist
#    (../_bootstrap/00-namespaces.yaml). Do not reapply/recreate them here.

kubectl apply -f 10-serviceaccount.yaml
kubectl apply -f 20-rbac.yaml
kubectl apply -f 30-token-secret.yaml
```

## Manual step after applying — build the kubeconfig

Applying these manifests does not produce a kubeconfig file by itself. Per
namespace, pull the token out of its Secret:

```bash
# pantry-bot
kubectl get secret ci-deploy-token -n pantry-bot -o jsonpath='{.data.token}' | base64 -d

# opsbot
kubectl get secret ci-deploy-token -n opsbot -o jsonpath='{.data.token}' | base64 -d

# observability (used by the Grafana deploy workflow)
kubectl get secret ci-deploy-token -n observability -o jsonpath='{.data.token}' | base64 -d
```

The cluster CA and API server address are NOT in these Secrets — pull them
from the existing (admin) kubeconfig instead:

```bash
kubectl config view --minify --raw
```

Take `.clusters[0].cluster.server` and `.clusters[0].cluster.certificate-authority-data`
from that output, combine with the token above, and assemble a minimal
kubeconfig (one `cluster`, one `user` with `token:`, one `context` pointing
at the target namespace). Three separate kubeconfigs, one per token — the
`pantry-bot` token cannot authenticate anything the `opsbot` Role wasn't
also bound to, and vice versa, since each RoleBinding only references the
ServiceAccount in its own namespace. Store the observability kubeconfig in
the repository secret `KUBE_CONFIG_GRAFANA` for the Grafana deploy workflow.

## What this deliberately does NOT grant

No pod mutation, no secrets (any verb), no exec, no delete, nothing cluster-scoped. Pod reads and pod logs are limited to post-rollout verification.
See `20-rbac.yaml`'s header comment for the full verb-by-verb rationale —
this is intentionally narrower than `../opsbot`'s own bot RBAC, which needs
`pods`/`pods/exec` for its Minecraft RCON console. A deploy pipeline doing
`kubectl set image` / `rollout status` / `rollout undo` has no equivalent
need.
