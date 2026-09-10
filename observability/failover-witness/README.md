# Lightweight failover witness

This is a deliberately small, private witness for PantryBot's fenced
failover controller. It stores a monotonically increasing fencing epoch and
grants one short-lived authority lease to either `home` or `oracle`.

It is not a database, a health detector, or a source-fencing mechanism. A
caller must still prove that the old PostgreSQL writer is stopped or rejects
writes before promoting a new writer. Automatic failover remains disabled
until that proof exists.

The service listens on localhost only. Each site can reach it through an
outbound SSH local-forward to GCP; no public application port or paid load
balancer is required. The shared secret belongs in a root-owned environment
file and must not be committed.

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
```

The service API is `GET /healthz`, `POST /v1/authority/acquire`, and
`POST /v1/authority/renew`, authenticated with `Authorization: Bearer ...`.

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
