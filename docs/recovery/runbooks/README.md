# Homelab service recovery runbooks

These are operator-facing SOPs for the non-Minecraft homelab services. Cartwise
is intentionally excluded. They complement, rather than replace, the design
and gate documents in `docs/recovery/`.

## Scope and safety boundary

The home and Oracle environments are independent k3s control planes. Do not
stretch k3s, share a local-path PVC over the WAN, or assume that two ready pods
make a stateful service active-active. The supported target is active-active
application capacity with one fenced writer or one fenced external-side-effect
owner.

Before any mutating command:

```bash
# Run against the intended site and do not proceed if the context is unclear.
kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
```

Use a separate kubeconfig for Oracle as described by the relevant migration
document; never paste its contents or a Secret value into a shell transcript.
The commands below are templates. Replace only the explicitly marked context,
namespace, pod, or deployment names after confirming them with `kubectl get`.

Read-only triage normally starts with:

```bash
kubectl get events -A --sort-by=.lastTimestamp | tail -80
kubectl -n <namespace> get deploy,sts,pods,svc,pvc -o wide
kubectl -n <namespace> describe pod <pod>
kubectl -n <namespace> logs <pod> --all-containers --since=30m --tail=200
```

Do not use `kubectl delete`, force-delete a pod, promote a database, or change
Cloudflare routing merely because a health check is red. First identify the
site, the current writer/lease epoch, the last known-good backup or WAL
position, and whether the other site can reach the authority. A timeout is not
proof that the old writer is fenced.

## Common incident record

Record all recovery work in the applicable GitHub Issue, principally homelab
[#191](https://github.com/ChayzX/k8s-homelab/issues/191) (cross-service
recovery), [#198](https://github.com/ChayzX/k8s-homelab/issues/198)/[#202](https://github.com/ChayzX/k8s-homelab/issues/202)
(Authentik), [#201](https://github.com/ChayzX/k8s-homelab/issues/201)
(Operations), [#203](https://github.com/ChayzX/k8s-homelab/issues/203)
(observability), [#204](https://github.com/ChayzX/k8s-homelab/issues/204) (CI
tunnel), [#200](https://github.com/ChayzX/k8s-homelab/issues/200) (Opsbot), and
[#262](https://github.com/ChayzX/k8s-homelab/issues/262) (JMusicBot). PantryBot
tracking remains in PantryBot #147/#148 and its own runbook; these edits do not
modify PantryBot documentation. Include UTC detection, diagnosis, failure
boundary, current owner/epoch, source backup or replication position,
promotion/fencing timestamps, RTO/RPO, route convergence, duplicate-side-effect
result, rollback result, and exact sanitized commands. Minecraft and Cartwise
are outside this runbook set.
Never record token/password/private-key values.

## Runbook index

| Service | Runbook | Current recovery boundary |
|---|---|---|
| PantryBot | [pantrybot.md](pantrybot.md) | HA application roles at both sites; database promotion, routing, and automatic fencing remain explicit gates. |
| Authentik / LDAP / PostgreSQL | [authentik.md](authentik.md) | Home primary plus Oracle physical standby; promotion and full provider/session reconstruction are not routine automation. |
| Operations | [operations.md](operations.md) | Home SQLite writer; Oracle isolated/read-only recovery target, currently not a second writable site. |
| Observability | [observability.md](observability.md) | Local collectors plus Grafana Cloud and external checks; histories are not locally multi-primary. |
| CI deployment tunnel | [ci-deployment-tunnel.md](ci-deployment-tunnel.md) | One deployment authority per target; connector failure must fail closed rather than race. |
| Opsbot | [opsbot.md](opsbot.md) | One Discord/Kubernetes/RCON side-effect owner; Oracle is gated standby/recovery. |
| JMusicBot | [jmusicbot.md](jmusicbot.md) | Home Discord voice owner; Oracle R2-backed standby and constrained-egress promotion. |

## Verification rule

A recovery is complete only when the service-specific verification section
passes and the known gates are updated with evidence. A pod restart, a 200 from
`/ready`, or a successful restore download alone is not promotion evidence.
