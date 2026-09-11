# Network Validation Matrix

**Status:** the home-cluster node-pair baseline was refreshed 2026-09-10 before
Oracle separation; the ChaseBot kubelet-proxy gate was remediated and
revalidated 2026-09-09. Oracle is now an independent k3s environment, so its
cross-environment paths are recovery/management paths rather than home-cluster
node paths.
The repeatable probe is `scripts/network-validation.sh`. ChaseBot-to-Oracle
management remains intentionally excluded; no WAN consensus or topology
migration depends on that path.

## Confirmed observation

From `chasebot` (`192.168.40.200`), Oracle Tailscale address `100.78.181.15` returned `Network is unreachable`. `chasebot` has wired LAN and flannel/CNI interfaces but no Tailscale interface. This explains why Kubernetes `Ready` does not prove a usable path between every node.

From Oracle, the current home API (`100.84.89.87:6443`) and home kubelet
(`100.84.89.87:10250`) are reachable, while chasebot's LAN kubelet
(`192.168.40.200:10250`) is not reachable from Oracle. This supports keeping
Oracle out of the home cluster's consensus path and treating cross-environment
connections as explicit application/recovery paths only.

The home tower also has a LAN address (`192.168.40.208`). From chasebot, the
LAN API endpoint (`192.168.40.208:6443`) and home NodePorts are reachable, but
the tower's Tailscale address (`100.84.89.87`) and Oracle's Tailscale address
(`100.78.181.15`) are not. This means chasebot has a usable LAN application
path, but not an independent management path to the advertised node address.
The root-required correction was applied on 2026-09-09: ChaseBot uses the
reserved LAN server endpoint `https://192.168.40.208:6443`; the tower advertises
that LAN address, includes it in `tls-san`, and uses K3s
`egress-selector-mode: disabled` so the API server connects directly to node
kubelets over the already-tested LAN/Tailscale paths. The pre-change tower
config is retained at `/etc/rancher/k3s/config.yaml.codex-pre-lan-advertise-20260909`.
The temporary ChaseBot passwordless-sudo grant used for the change was removed.

The 2026-09-09 baseline also confirmed from the tower that its own API and
kubelet, chasebot SSH, and Oracle SSH are reachable. From chasebot, the home
API, home kubelet, and Oracle SSH all failed. From Oracle, the home API and
kubelet succeeded while the chasebot kubelet failed. These are TCP reachability
tests only; they do not authorize a topology change.

A temporary BusyBox probe on the home node resolved CoreDNS and reached both
the PantryBot and Authentik ClusterIP health endpoints, as well as external
DNS. The analogous `kubectl exec` into the chasebot probe failed through the
API server's kubelet proxy with `502 Bad Gateway` while dialing
`192.168.40.200:10250`; a raw TCP check from the tower still reached that
port. This narrows the chasebot issue from simple port closure to kubelet
proxy/TLS/routing behavior and leaves pod-network evidence from chasebot
unproven. The temporary probes were deleted after the test.

The 2026-09-10 host-network probe adds evidence from chasebot: CoreDNS resolves
`kubernetes.default.svc` to `10.43.0.1`, and PantryBot, Authentik, and Grafana
service and pod health endpoints return HTTP 200. Thus chasebot's pod/service
network is functional even though its Tailscale management path is absent.
Before remediation, the API server's `/api/v1/nodes/chasebot/proxy/healthz`
returned 502 while the other two nodes returned `ok`; direct TLS/TCP access to
ChaseBot `10250` worked. After the server advertisement/egress correction and
restart, the same proxy endpoint returns `ok` for `minecraftmachine`,
`chasebot`, and `pantry-bot-oracle`; no stale Tailscale endpoint errors appeared
in ChaseBot's post-change agent journal.

The complete source matrix was rerun on 2026-09-10 before Oracle separation:

- **Tower:** all management, kubelet, DNS, service/pod, and NodePort checks
  passed.
- **chasebot:** LAN API/SSH, local kubelet, DNS, service/pod, and NodePorts
  passed; Tailscale home API, Oracle SSH/kubelet, and Tailscale home kubelet
  failed because chasebot has no Tailscale interface and no route to Oracle.
- **Oracle:** home Tailscale SSH/kubelet, local SSH/kubelet, DNS,
  service/pod checks passed; home LAN, chasebot, and home NodePort checks
  failed as expected because Oracle is not on the home LAN and chasebot is not
  reachable through an intentional cross-site path.

These failures are explicit topology findings, not transient probe errors.
The supported control-plane path is now the documented stable LAN endpoint for
ChaseBot. Tailscale remains the overlay path for Oracle and the tower; it is not
required on ChaseBot for Kubernetes API/kubelet access.

After separation, the home cluster contains only `minecraftmachine` and
`chasebot`; Oracle's independent server is reachable over its Tailscale address
and does not participate in home API, kubelet, or consensus traffic.

## Workload-level witness path

The host-level SSH forwards are intentionally node-local. The home units bind
their GCP forwards to `127.0.0.1:18765`, and
`observability/failover-witness/failover-witness-relay.yaml` runs one
host-networked relay on each home node. The relay exposes port 18766 on its
Kubernetes internal node address and forwards only to that node's loopback tunnel. The
`failover-witness-relay` ClusterIP Service maps port 18765 to the relay and
sets `internalTrafficPolicy: Local`, so a workload using
`failover-witness-relay.observability.svc.cluster.local:18765` stays on the
node-local relay.

This closes the previous host-only evidence gap without stretching the k3s
control plane or exposing the witness publicly. It is deliberately fail-closed:
if a node-local relay is unavailable, a workload does not fall back to the
other home node's witness tunnel. The relay does not hold the witness secret;
callers remain responsible for Bearer authentication. The path still needs a
live home tunnel unit on each node and must be validated from an actual
workload after installation; this repository change does not apply or restart
live services.

Validation after installation (the second command creates and removes a
temporary probe pod):

```sh
kubectl -n observability get ds/failover-witness-relay \
  svc/failover-witness-relay pods -o wide
kubectl -n observability run witness-path-check --rm -i --restart=Never \
  --image=busybox:1.36 -- wget -qO- \
  http://failover-witness-relay.observability.svc.cluster.local:18765/healthz
```

Repeat the check with a pod pinned to each home node before treating workload
reachability as proven. Do not use the fixed tower address as a replacement:
that would reintroduce the single-node dependency this relay is intended to
remove.

## Required matrix

| Source | Destination | Test | Required result |
|---|---|---|---|
| chasebot | Oracle | route, ICMP/TCP, management SSH | Reachable through an intentional path |
| Oracle | chasebot | route, kubelet/API-required ports | Reachable or explicitly excluded |
| every node | current API server | k3s control-plane ports | Stable and monitored |
| every node | every required pod endpoint | DNS, service, pod IP, MTU | Expected application traffic works |
| external observer | public endpoints | HTTPS/TLS and application health | Alert distinguishes tunnel from origin failure |
| backup worker | R2/object storage | upload, list, download, checksum | Versioned generation succeeds |

## Acceptance conditions

- Routes and interfaces are documented, not inferred from node Ready status.
- No recovery path depends on `minecraftmachine` acting as an unrecorded VPN/router gateway.
- Pod and Service CIDRs do not overlap with connected environment networks.
- MTU and packet loss are tested over a sustained sample, not a single ping.
- External alert delivery is confirmed while the home API is unavailable.
