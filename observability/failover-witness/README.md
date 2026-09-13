# Lightweight failover witness

This is a deliberately small, private witness for PantryBot's fenced
failover controller. It stores a monotonically increasing fencing epoch and
grants one short-lived authority lease to either `home` or `oracle`.
Multiple replicas from the current site receive the same epoch/token, so a
site can scale its worker role horizontally without granting authority to the
other site.

It is not a database, a health detector, or a source-fencing mechanism. A
caller must still prove that the old PostgreSQL writer is stopped or rejects
writes before promoting a new writer. Automatic failover remains disabled
until that proof exists.

## Minecraft-safe PantryBot writer fence

`fence-pantry-postgres.sh` is the source-side fence contract for the home
PantryBot database. It scales down and force-removes only the named PantryBot
PostgreSQL StatefulSets, verifies that their services have no endpoints, and
fails closed if the Kubernetes API or any database pod remains reachable. It
does not stop `k3s`, `k3s-agent`, containerd, a node, or Minecraft. The
repository contract test is `test_pantry_postgres_fence.py`.

Install it only on the source-writer host that owns the local PostgreSQL
authority (currently ChaseBot during normal home-primary operation). Do not
install this command on MinecraftMachine; Minecraft shares its control plane
with the home standby and is intentionally excluded from the fence target:

```sh
sudo install -o root -g root -m 0755 \
  observability/failover-witness/fence-pantry-postgres.sh \
  /usr/local/sbin/fence-pantry-postgres
```

The command is destructive fencing, not a health check. `--help` is the only
non-mutating invocation. The existing GCP reverse-SSH endpoint is a forced
operation on ChaseBot and currently rejects arguments; inspect or replace
that forced operation only through the ChaseBot maintenance path. Do not test
the fence through production SSH until a maintenance window has recorded the
expected standby restore and stale-writer proof in GitHub Issues #147 and
#191.

The service listens on localhost only. Each site can reach it through an
outbound SSH local-forward to GCP; no public application port or paid load
balancer is required. The shared secret belongs in a root-owned environment
file and must not be committed.

The home tunnel units bind to host loopback (`127.0.0.1:18765`). Kubernetes
workloads reach the local tunnel through
`failover-witness-relay.observability.svc.cluster.local:18765`, which is
backed by a host-networked DaemonSet and a Service with
`internalTrafficPolicy: Local`. That policy deliberately sends a workload to
the relay on its own home node and fails closed if that node has no relay; it
does not silently send coordination traffic across the home pair.

The relay listens on the node's Kubernetes internal address at port 18766 and
forwards only to that node's loopback tunnel. It adds no authentication: callers still need
the witness Bearer secret, and the relay is intended only for the private home
network. The Oracle tunnel remains a node-address listener until an equivalent
Oracle relay is deployed.

Before enabling the unit, create its unprivileged account once:

```sh
sudo useradd --system --home-dir /var/lib/failover-witness \
  --no-create-home --shell /usr/sbin/nologin failover-witness
sudo install -d -o failover-witness -g failover-witness -m 0750 \
  /var/lib/failover-witness
```

Run the unit test with:

```sh
python3 observability/failover-witness/test_witness.py
python3 observability/failover-witness/test_relay.py
```

The workload relay is defined in
`failover-witness-relay.yaml`. Apply it after the `observability` namespace
exists:

```sh
kubectl apply -f observability/namespace.yaml
kubectl apply -f observability/failover-witness/failover-witness-relay.yaml
```

Install the revised home tunnel units on both home hosts, then restart the
corresponding unit so the SSH forward moves from the LAN address to loopback:

```sh
sudo systemctl daemon-reload
sudo systemctl restart failover-witness-home-tunnel.service
# On chasebot, use the same commands for failover-witness-chasebot-tunnel.service.
```

Workloads should use the Service DNS name above rather than either host LAN
address. A node-local relay is a reachability mechanism, not failover proof:
the witness still requires a valid secret, and database writer fencing and
promotion remain separate gates.

The service API is `GET /healthz`, `POST /v1/authority/acquire`, and
`POST /v1/authority/renew`, authenticated with `Authorization: Bearer ...`.

## PantryBot PostgreSQL transport

The repository also contains a guarded transport pair for the home PantryBot
authority:

- `pantry-bot-postgres-home-tunnel.service` runs on `chasebot` and reverse-
  forwards the ClusterIP authority to GCP loopback port `25432`.
- `pantry-bot-postgres-oracle-forward.service` runs on Oracle and forwards its
  node-local `100.78.181.15:25432` to that GCP loopback port.

The two SSH identities remain site-local. This is encrypted transport for a
standby rehearsal, not database replication, promotion, or writer fencing.
Install and test these units independently before creating a replication role;
do not expose port `25432` publicly or route application traffic through it.

After home failback, the direct home-to-Oracle return transport is provided by
`pantry-bot-postgres-home-return-tunnel.service`. It binds only Oracle's
loopback `127.0.0.1:25432` and forwards to the home PostgreSQL replication
NodePort `127.0.0.1:30432`; Oracle's standby Secret must use `PRIMARY_HOST`
`127.0.0.1`, `PRIMARY_PORT` `25432`, and slot `pantry_oracle_return`.

The repository also includes the home-side tunnel unit. Install it only on a
site that has its own SSH identity authorized on GCP; never copy the home
private key to Oracle:

```sh
sudo install -o root -g root -m 0644 \
  observability/failover-witness/failover-witness-home-tunnel.service \
  /etc/systemd/system/failover-witness-home-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now failover-witness-home-tunnel.service
```

Oracle uses the separate `failover-witness-oracle-tunnel.service` unit and
the Oracle-generated `/home/ubuntu/.ssh/gcp-witness-oracle` key. Its GCP OS
Login public key must be added for the service-account OS Login username shown
by `gcloud beta compute os-login ssh-keys add`; do not reuse the home key.

The home worker node uses `failover-witness-chasebot-tunnel.service` with a
separate `/home/cpederson/.ssh/gcp-witness-chasebot` key and binds to the
ChaseBot node address. This keeps home pod access to coordination available
when MinecraftMachine is unavailable; the witness remains authenticated and
private to the LAN path.
