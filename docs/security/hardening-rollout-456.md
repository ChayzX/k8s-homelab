# Kubernetes hardening rollout — issue #456, batch 1

## Scope and risk

This change removes cluster-wide `secrets` list/watch from the `observability-kube-state-metrics` ClusterRole and adds `readOnlyRootFilesystem: true` to five already non-root, no-service-account-token Cloudflare connector containers.

The manifest changes do **not** alter credentials, routes, pod ports, images, or storage. They do change security restrictions and may restart a pod if manifests are applied, so **do not blindly apply them to production**. Cloudflared could depend on an unexpected writable path; test one connector first.

The broad `homelab-admin` ClusterRole intentionally belongs to a named human identity; no privilege removal is attempted without user confirmation. Stateful PostgreSQL, Grafana, Authentik, monitoring agents, and failover workloads remain unchanged. Additional Trivy warnings remain tracked in issue #456.

## Gate before rollout

1. Review the PR CI contract and Trivy results. Run `python3 tests/security-hardening-contract.py` locally.
2. Use a non-production tunnel where possible, using the same pinned image/digest. Confirm it starts with read-only root and stays Ready; check logs for filesystem permission errors.
3. Confirm whether any dashboards rely on `kube_secret_*` metrics before applying the `kube-state-metrics` Role change. Those metrics will no longer be available. Test `kubectl auth can-i list secrets --as=system:serviceaccount:observability:kube-state-metrics-sa --all-namespaces` after applying; expected: `no` unless another Role grants it.
4. Apply only to the intended cluster/context, one manifest at a time. Use `kubectl diff -f <file>` and `kubectl apply --dry-run=server -f <file>` first, reviewing all changes before proceeding. Note: multi-document manifests include ServiceAccounts/Services and should be reviewed as a whole.
5. After applying each connector, check rollout, `/ready` metrics endpoint, Cloudflare tunnel connectivity, expected hostname routing, and application error rate. Stop if traffic fails.

## Checks and rollback

For each connector:
```sh
kubectl -n <namespace> rollout status deployment/<deployment> --timeout=180s
kubectl -n <namespace> logs deployment/<deployment> -c cloudflared --tail=100
```

If a connector fails after applying this change, restore the prior manifest revision from Git and apply that reviewed version, or use `kubectl -n <namespace> rollout undo deployment/<deployment>` when a prior Deployment revision is available. Verify traffic and readiness again.

If kube-state-metrics loses non-secret metrics, restore only its prior RBAC YAML from Git after review and verify list/watch authorization and scrape health. Avoid automatically granting Secret access back simply to clear warning logs.

## In scope (five connectors)
- ci-tunnel/cloudflared
- observability/mcp-grafana-cloudflared
- observability/windows-alloy-metrics-cloudflared
- pantry-bot/commands-cloudflared
- pantry-bot/app-cloudflared

The `windows-alloy-metrics-cloudflared` Deployment also contains an nginx `write-gateway` container, which is intentionally unchanged.
