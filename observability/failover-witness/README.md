# Lightweight failover witness

A small, private lease witness that runs on the GCP e2-micro `discordmusicbot`
(`failover-witness.service`, `witness.py`). It stores a monotonically
increasing fencing epoch and grants one short-lived authority lease per
resource to one site at a time.

**Current consumers** (all separate from PantryBot):

- **opsbot** and **JMusicBot**: their Deployments hold a resource-scoped
  lease before connecting to Discord (`opsbot-witness`, `jmusicbot-witness`
  Secrets).
- **Authentik PostgreSQL** standby design:
  `authentik_oracle_promoter.py`, and the `fence-authentik-*.sh` scripts.

**PantryBot no longer uses the witness.** It runs only on Oracle, an
independent single-node k3s cluster, with no standby, fencing or failover.
The PantryBot home/Oracle/Canada promoters, fences, standby followers, Canada
scripts and PantryBot fencer RBAC were removed from this directory. See git
history for the retired design.

`oracle_promoter.py` and its two `pantry-postgres-oracle-promoter.*.example`
test fixtures remain only because `authentik_oracle_promoter.py` imports
`OraclePromoter`, `PromotionAdapters` and helpers from it. Do not install
the PantryBot promoter unit.

It is not a database, a health detector, or a source-fencing mechanism. A
caller must still prove that the old writer is stopped or rejects writes
before promoting a new one.

## Network path

The service listens on localhost on the GCP VM. Each site reaches it through
an outbound SSH local-forward; no public application port or load balancer is
needed. The shared secret belongs in a root-owned environment file and must
not be committed.

- `failover-witness-home-tunnel.service` (minecraftmachine) binds host
  loopback `127.0.0.1:18765`. (The chasebot tunnel and fence-tunnel units were
  removed when chasebot left the cluster on 2026-09-29.)
- `failover-witness-oracle-tunnel.service` does the same on Oracle.

Kubernetes workloads on the home cluster reach the local tunnel through
`failover-witness-relay.observability.svc.cluster.local:18765`, backed by the
host-networked DaemonSet in `failover-witness-relay.yaml` and a Service with
`internalTrafficPolicy: Local`. That policy sends a workload to the relay on
its own node and fails closed if that node has no relay. It never sends
coordination traffic across nodes. The relay listens on the node's internal
address at port 18766 and forwards only to that node's loopback tunnel. It
adds no authentication: callers still need the witness Bearer secret.

## Install

Before enabling the witness unit, create its unprivileged account once:

```sh
sudo useradd --system --home-dir /var/lib/failover-witness \
  --no-create-home --shell /usr/sbin/nologin failover-witness
sudo install -d -o failover-witness -g failover-witness -m 0750 \
  /var/lib/failover-witness
```

Apply the relay after the `observability` namespace exists:

```sh
kubectl apply -f observability/namespace.yaml
kubectl apply -f observability/failover-witness/failover-witness-relay.yaml
```

The service API is `GET /healthz`, `POST /v1/authority/acquire`, and
`POST /v1/authority/renew`, authenticated with `Authorization: Bearer ...`.

## Tests

```sh
cd observability/failover-witness
python3 test_witness.py
python3 test_relay.py
python3 test_command_policy.py
bash test_authentik_home_fence_transport.sh
# pytest-style: test_fence_agent.py, test_oracle_promoter.py,
# test_authentik_oracle_promoter.py
```
