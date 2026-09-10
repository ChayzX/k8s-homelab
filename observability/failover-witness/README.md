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

Run the unit test with:

```sh
python3 observability/failover-witness/test_witness.py
```

The service API is `GET /healthz`, `POST /v1/authority/acquire`, and
`POST /v1/authority/renew`, authenticated with `Authorization: Bearer ...`.
