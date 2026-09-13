# CI deployment tunnel Oracle standby

The CI connector is privileged infrastructure. The Oracle target must remain
disabled until it has its own Cloudflare Access route, service-token policy,
Oracle Kubernetes API endpoint, and least-privilege Oracle kubeconfig. The
overlay renders the connector at zero replicas and does not reuse the home
deployment credential or claim dual-site workflow failover.

```bash
kubectl kustomize docs/recovery/ci-tunnel-oracle-standby
kubectl apply -k docs/recovery/ci-tunnel-oracle-standby
```

Promotion requires a separate Oracle tunnel/hostname or an independently
validated route, a distinct Access service token, a distinct GitHub Actions
secret, and a target-specific kubeconfig limited to the intended namespaces.
After those are staged, scale the connector explicitly and test a workflow
against Oracle while the home connector is fenced. Roll back by scaling the
Oracle connector to zero and restoring the prior workflow target.

This is tracked by homelab Issue #204. It does not change the live home
connector or Cloudflare configuration.

## Workflow concurrency contract

Every non-Minecraft deployment workflow that can mutate a shared target uses a
static, target-scoped GitHub Actions concurrency group with
`cancel-in-progress: false`. A later manual run waits for the earlier rollout
and verification to finish instead of cancelling an in-flight change. This
contract is present on the Grafana rollback, Windows Alloy, Opsbot, and
JMusicBot workflows. The Minecraft workflow has its own separate group and is
outside this project’s implementation scope.

The contract also bounds usage: multiple queued requests for the same target do
not execute concurrently. It does not replace target identity checks,
Cloudflare Access fencing, or the Oracle credential gate above.
