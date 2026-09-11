# Chasebot management-path remediation

## Observed state

As of 2026-09-10:

- `chasebot` is Ready and its host can reach the home LAN API at
  `192.168.40.208:6443`.
- Its pod/service network works: CoreDNS, PantryBot, Authentik, Grafana, and
  home NodePorts were all reachable from the chasebot host.
- `chasebot` has no `tailscale0` interface, so it cannot reach the advertised
  home node address `100.84.89.87` or Oracle `100.78.181.15`.
- Direct TLS/TCP access to `chasebot:10250` works from the tower, but the
  Kubernetes API proxy returns HTTP 502 for that node. The other two node
  proxies return `ok`.

This is a management-path and kubelet-proxy gate, not evidence that the pod
network is broken.

## Preferred maintenance path

Authenticate Tailscale on chasebot and keep the existing k3s agent endpoint
unchanged. This matches the current Oracle/flannel design and avoids changing
the home control-plane address or rotating the API certificate.

Run as root on chasebot during a maintenance window:

```bash
systemctl stop k3s-agent
apt-get update
apt-get install -y tailscale
tailscale up
tailscale ip -4
systemctl start k3s-agent
```

Use the approved tailnet/authentication method for this host. Do not put an
auth key in GitHub, this repository, or a shell transcript.

Before changing k3s, record:

```bash
ip -br addr
ip route
systemctl cat k3s-agent
systemctl is-active k3s-agent
```

Then run `scripts/network-validation.sh` from the tower and chasebot, and
verify:

```bash
kubectl get node chasebot
kubectl get --raw=/api/v1/nodes/chasebot/proxy/healthz
kubectl get --raw=/api/v1/nodes/pantry-bot-oracle/proxy/healthz
```

The expected result is `Ready`, `ok`, and `ok`. Also verify that all existing
pods remain Ready and that the external monitor remains healthy.

## Rollback

If the agent does not reconnect, restore the previous Tailscale state and
restart only `k3s-agent`; do not delete the node or alter cluster state:

```bash
systemctl stop k3s-agent
tailscale down
systemctl start k3s-agent
```

If the preferred path is unavailable, the LAN endpoint alternative requires a
separate approved change to the agent server URL, node/API advertised address,
and certificate SANs. Do not attempt that alternative without a verified k3s
datastore/token backup and a rollback plan.
