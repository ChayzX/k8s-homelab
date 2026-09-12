# CI deployment tunnel disaster recovery SOP

## Current topology and authority

The connector is `cloudflared` in namespace `ci-tunnel`, defined by
`ci-tunnel/20-deployment.yaml`, using the Secret `ci-tunnel-token`. Its
Cloudflare hostname/origin configuration is managed outside Git and documented
in `ci-tunnel/MANUAL-SETUP.md`. The tunnel reaches Kubernetes deployment APIs;
it is a privileged deployment path, not a general application tunnel.

One workflow/connector must be the active deployment authority for a target
cluster. Do not run home and Oracle connectors against the same Kubernetes API
route unless Access, credentials, target identity, and stale-target rejection
have been separately proven. Oracle is a standby gate, not automatically a
second live deployment target.

## Tracking and evidence

Record connector, Cloudflare route, scoped-RBAC, target-selection, stale-target,
and rollback evidence in [homelab #204](https://github.com/ChayzX/k8s-homelab/issues/204).
Record cross-service capacity and recovery timing in
[homelab #191](https://github.com/ChayzX/k8s-homelab/issues/191). Identify the
selected site and workflow run for every mutating test.

## Normal health check

```bash
kubectl -n ci-tunnel get deploy,pods,svc -o wide
kubectl -n ci-tunnel describe deployment/cloudflared
kubectl -n ci-tunnel logs deployment/cloudflared --since=30m --tail=200
kubectl -n ci-tunnel get secret ci-tunnel-token -o name
```

The connector's `/ready` metrics endpoint must be green; process existence is
not enough. Validate the GitHub workflow target and Kubernetes API identity
without printing token contents.

## Read-only triage

Before changing a connector, tunnel, Secret, or workflow target, run:

```bash
kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
kubectl -n ci-tunnel get deploy,pods,svc,endpoints
kubectl -n ci-tunnel get events --sort-by=.lastTimestamp | tail -80
kubectl -n ci-tunnel get secret ci-tunnel-token -o name
```

Inspect Cloudflare connection/route, GitHub run metadata, target-site identity,
and `kubectl auth can-i` results read-only. A connected tunnel is not proof the
API route is correct, and a successful job is not proof that only one cluster
received the mutation.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Deployment unavailable or `/ready` fails | Events, previous logs, image digest, Secret key name, Cloudflare connection state | `kubectl -n ci-tunnel rollout restart deployment/cloudflared`; roll back the image/config if registration fails. | `/ready`, Cloudflare connection, and a dry-run/read-only API check pass. |
| Home node/site loss | CI connector unavailable, site management is lost, or home API route fails | Determine whether the API is actually unavailable and whether Oracle has independent capacity; connector health is not API health | Do not send workflow jobs to Oracle without an explicitly scoped Oracle target/credential. Promote only through a controlled deployment-target change. | A non-production deployment to the intended target records target/site identity and succeeds. |
| WAN partition | GitHub Actions cannot reach the intended private API while either site may still run | Check Cloudflare edge, both connectors, origin API, workflow target, and run state separately | Disable/reject jobs targeting an unavailable or ambiguous site; use Oracle only if its independent connector and RBAC are prepared. Never let both connectors race one rollout or promote on timeout alone. | Stale home target is rejected, Oracle target is explicit, and rollback to home is possible. |
| Oracle outage | Oracle connector/target unavailable | Verify home route and workflow target; do not alter home credentials | Keep the home deployment authority. Repair Oracle connector later; no workflow should silently fail over to an unverified target. | Home deployment works and workflow logs identify home, not ambiguous “default.” |
| Split brain or stale connector | Two tunnel connections receive the same workflow; unexpected cluster mutation | Inspect Cloudflare connections, workflow run target variables, RBAC identity, and recent rollout events | Stop/disable the stale connector or route, rotate its token if necessary, and preserve the known-good connector. Do not deploy until only one target is selected. | A harmless non-production rollout appears in exactly one cluster; stale target receives no mutation. |
| Kubernetes/API, storage, or credential failure | HTTP 401/403/5xx, RBAC denial, TLS validation error, or an unexpected target state | Check connector readiness, API origin CA setting, Service endpoint, workflow token Secret name, and `kubectl auth can-i` using the intended identity. This tunnel has no application database; inspect the target cluster's deployment state rather than inventing one. | Restore the documented Cloudflare origin/TLS settings and least-privilege Secret; roll back route/token version or the target deployment revision. Do not broaden RBAC as a diagnostic shortcut. | Read-only API, authorized deployment patch/status, and rollback operation pass for only the intended namespace/resources. |
| Secret/certificate failure | Connector cannot register or Cloudflare Access rejects workflow | Check key names and expiry/status without printing values | Recreate `ci-tunnel-token` from approved Cloudflare access, restart connector, or roll back. Never commit the token. | Connector `/ready`, workflow auth, and target identity validation pass. |
| Resource exhaustion | Connector OOM/restarts, API saturation, stuck workflow | `kubectl top`, events, connector logs; inspect workflow concurrency | Bound workflow concurrency and connector resources; cancel stale workflow only through GitHub; do not scale competing deployment authorities. | One rollout at a time, no duplicate mutation, and connector stable under representative load. |
| Rollback/failback | Failed rollout or route change | Preserve GitHub run, image digest, target/site, and rollout events | Use workflow's documented `rollout undo` path for the target; restore prior tunnel route/token, then re-enable home only after Oracle jobs are stopped. | Deployment revision, route, and target identity all match the selected site. |

## Safe recovery sequence

1. Stop new deployment runs targeting the unavailable or ambiguous site.
2. Verify the connector and API origin independently; do not infer one from the
   other.
3. Select exactly one target and record its site identity in the GitHub Issue.
4. Run a non-production or harmless deployment through the intended workflow.
5. Re-enable normal deploys only after the stale route/connector is proven not
   to receive mutations.

## Promotion and failback gates

Cross-site CI promotion is manual and fail-closed. It requires independent
management access, a named target site, connector/API identity checks,
least-privilege RBAC, an explicit workflow target, stale-target rejection, and
a harmless deployment observed in exactly one cluster. Failback stops
Oracle-targeted jobs and connector authority first, restores the known-good
home route/credential, and repeats the single-target test. Two connected
Cloudflare connectors are not active-active deployment authority.

## Verification checklist

Record in #204 and #191: connector readiness and connection/origin; workflow
run ID, image digest, and target site; read-only API and scoped `can-i` checks;
one observed harmless mutation; stale-target rejection; rollback result; and
route/credential restoration. Never record token contents.

## Known gates

- Cloudflare tunnel ingress is external configuration.
- No automatic cross-site CI promotion is proven until stale-target rejection,
  scoped credentials, and a controlled non-production deployment are recorded.
- The tunnel must never be used as a backdoor to grant broad cluster admin.
