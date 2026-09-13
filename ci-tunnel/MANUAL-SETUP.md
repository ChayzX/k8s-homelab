# CI tunnel manual setup

The connector pod is tracked here; the Cloudflare tunnel, public hostname,
Access policy, and service token remain external configuration. The Oracle
overlay is intentionally zero replicas until its independently scoped route,
token, and least-privilege kubeconfig are validated.

## Oracle standby contract

Create a separate Cloudflare tunnel and Access application for the Oracle
Kubernetes API. Do not reuse the PantryBot tunnel or the home CI token. Store
the Oracle tunnel token only as `ci-tunnel-token-oracle` in the Oracle
`ci-tunnel` namespace, then render and inspect the overlay:

```bash
kubectl --context <oracle-context> -n ci-tunnel create secret generic \
  ci-tunnel-token-oracle --from-literal=TUNNEL_TOKEN='<oracle-token>'
kubectl --context <oracle-context> apply -k ci-tunnel-oracle
kubectl --context <oracle-context> -n ci-tunnel scale \
  deployment/cloudflared-oracle --replicas=1
```

Enable it only after the Access policy is service-token-only, the origin is
the Oracle Kubernetes API, and the workflow kubeconfig is scoped to the
intended Oracle namespaces/resources. Keep the deployment at zero replicas
until those external gates are complete.
