> **Historical:** describes the retired multi-site / Grafana Cloud setup (PantryBot home, Canada and failover topology; Grafana Cloud). Current state: PantryBot runs only on the Oracle node (single-site since 2026-09-25) with self-hosted Grafana/Prometheus/Loki in the `observability` namespace. Body left unchanged as a dated record.

# Canada replication transport

Canada PostgreSQL currently listens on `127.0.0.1:15432`. The prepared tunnel
forwards Oracle loopback `127.0.0.1:25442` to that Canada endpoint, using the
BotAdmin-managed SSH key. It is intentionally activation-window only; do not
start it until the isolated standby-prep configuration is applied and an
operator has verified the tunnel and replication credentials. The standby
consumes `PRIMARY_PORT=25442`.