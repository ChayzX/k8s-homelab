# Opsbot disaster recovery and ownership SOP

## Current topology and authority

Opsbot is the Python Discord bot in namespace `opsbot`, defined by
`opsbot/40-deployment.yaml` with RBAC in `opsbot/20-rbac.yaml`. It can inspect
allow-listed workloads and perform limited deployment/RCON operations. It also
has a GitHub Issues path for `/bug`; GitHub Issues is the only project tracker.

The bot must acquire the neutral `opsbot-witness` site lease before opening a
Discord gateway. Lease loss must close the gateway and fail closed for
Kubernetes/RCON mutations. `opsbot/OWNERSHIP-REHEARSAL.md` says the production
gate requires real Discord credentials, witness reachability, both site
deployments, stale-owner rejection, and a disposable mutation check. Oracle
must remain zero replicas until that gate is complete.

## Tracking and evidence

Record Opsbot lease, Discord ownership, RBAC, Kubernetes/RCON mutation, and
promotion/failback evidence in [homelab #200](https://github.com/ChayzX/k8s-homelab/issues/200).
Record cross-service capacity and recovery timing in
[homelab #191](https://github.com/ChayzX/k8s-homelab/issues/191). Issue #200 is
the authority for whether Oracle may leave its gated standby state.

## Normal health check

```bash
kubectl -n opsbot get deploy,pods,svc -o wide
kubectl -n opsbot describe deployment/opsbot
kubectl -n opsbot logs deployment/opsbot --since=30m --tail=200
kubectl -n opsbot get secret -o name
```

Do not print Discord/GitHub token values. Verify the logs show the expected
site lease and Discord connection, not merely a running Python process.

## Read-only triage

Before restarting, scaling, rotating credentials, or granting permission:

```bash
kubectl config current-context
kubectl cluster-info
kubectl get nodes -o wide
kubectl -n opsbot get deploy,pods,svc,endpoints
kubectl -n opsbot get events --sort-by=.lastTimestamp | tail -80
kubectl -n opsbot get secret -o name
kubectl -n opsbot logs deployment/opsbot --since=30m --tail=200
```

Read the witness lease/epoch, replica state, and `kubectl auth can-i` results
without printing token data. A Ready pod does not prove it owns Discord or may
mutate Kubernetes/RCON.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process/pod crash | Pod restart/unavailable, Discord disconnect, health endpoint/logs | Events, previous logs, image/Secret names, witness response; do not start Oracle automatically | Restart or roll back the home Deployment. It must reacquire the lease before gateway/mutation behavior. | One Discord connection, lease current, harmless status command succeeds, and no duplicate mutation. |
| Home node/site loss | Home pod/node unavailable, site management is lost, or Discord bot absent | Check Oracle isolation and witness reachability; identify whether home API/RCON targets still exist | Do not start Oracle as a second Discord owner. Promote only after the witness and stale-home lease rejection gate passes. | Oracle gets a higher epoch, home stale token is rejected, Discord connects once, disposable mutation succeeds. |
| WAN partition | Discord commands or witness renewals fail while either site may still run | Distinguish Discord, witness, Kubernetes API, and site/WAN failure; inspect both lease epochs | If witness cannot establish safe ownership, keep Oracle stopped/fail closed. No blind takeover or promotion on timeout alone. | One owner and explicit downtime reason; no duplicate Discord gateway or mutation. |
| Oracle outage | Standby unavailable while home owns lease | Verify home lease/Discord connection; do not change home | Leave home owner active and repair Oracle capacity later. | Home commands/status/RCON remain one-owner and functional. |
| Split brain/lease loss | Two bots connected, lease renewal rejected, duplicate replies/mutations | Capture both logs and witness epoch; disable external side effects first | Lease-losing process must disconnect/reject mutation. Fence/stop stale pod and rotate credentials only if compromise is suspected. | Exactly one Discord gateway, stale token rejected, audit log shows no duplicate authorized mutation. |
| Database/storage or bot-local state/RBAC corruption | Startup config failure, unauthorized API calls, `/bug` errors, or missing local state | Opsbot has no approved shared application database; inspect ConfigMap/Secret names, RBAC manifests, allowlist, and pod storage without inventing a restore source. Never grant ClusterRole admin to “test”. | Restore tracked manifest/image and approved Secrets; roll back image/config. If a future state store is added, restore it in isolation before starting a side-effect owner. | Status command only reaches allowed resources, `/bug` creates the intended GitHub Issue, mutation audit is present, and no stale state repeats a mutation. |
| Secret/certificate failure | Discord login failure, GitHub 401, witness 401/TLS error | Check key names, expiry, endpoint, and response code without printing values | Reconstruct `opsbot-discord`, `opsbot-github`, and witness credentials from escrow; restart one owner. | Gateway login, witness `/healthz`, harmless GitHub issue dry path, and Kubernetes read pass. |
| Routing/tunnel failure | Discord is outbound and works, but Operations/monitoring evidence is absent | Check logs and observer paths; Opsbot has no inbound public route by design | Repair only its outbound DNS/egress or witness path; do not expose a new inbound endpoint. | Discord command and audit log work; observer sees service state. |
| Resource exhaustion | OOM/restarts, Discord heartbeat lag, command timeout | `kubectl top`, events, Python logs; identify long-running `/pods exec` timeout | Bound concurrency/timeouts, stop expensive diagnostics, or roll back image. Do not add an Oracle owner to compensate. | Heartbeats stable, bounded command completion, no duplicate mutation. |
| Rollback/failback | Oracle test/promotion or new image fails | Preserve witness epoch, bot logs, target, and audit entries | Stop Oracle and verify its stale epoch is rejected; start home only after the witness grants a fresh lease. Roll back image with `rollout undo` if needed. | Home is sole owner, Oracle cannot connect/mutate, and the issue records timestamps and duplicate result. |

## Ownership rehearsal

Use the exact five-step gate in `opsbot/OWNERSHIP-REHEARSAL.md`: provision the
same witness secret at both sites, start home only, prove Oracle is rejected,
let home lease expire under controlled isolation, start Oracle at a higher
epoch, and prove home's stale token cannot renew. Use a disposable/non-
production deployment for the mutation check.

## Promotion and failback gates

Promotion is manual and fail-closed: independent access to the surviving site,
witness reachability, a higher lease epoch, stale-home lease rejection, valid
Discord/GitHub/Kubernetes/RCON credentials, and a disposable mutation check are
all required. Failback stops Oracle and proves its stale epoch cannot renew or
mutate before home obtains a fresh lease. Replica count or readiness is never a
side-effect fence.

## Verification checklist

Record in #200 and #191: witness epoch and lease holder; exactly one Discord
gateway; stale-owner rejection; allowed read-only API checks; one disposable
mutation and audit result; credential status without values; resource headroom;
and rollback/failback timestamps. If an ownership check is unavailable, keep
the service stopped or fail closed.

## Known gates

- A second Ready pod is unsafe while both can open Discord or mutate APIs.
- The witness is not a physical fence for a host that still has valid Discord,
  Kubernetes, or RCON credentials.
- Preserve the narrow RBAC in `opsbot/20-rbac.yaml`; never solve recovery by
  granting cluster-admin.
