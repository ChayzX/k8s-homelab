> **Historical:** describes the retired multi-site / Grafana Cloud setup (PantryBot home, Canada and failover topology; Grafana Cloud). Current state: PantryBot runs only on the Oracle node (single-site since 2026-09-25) with self-hosted Grafana/Prometheus/Loki in the `observability` namespace. Body left unchanged as a dated record.

# Oracle promoter hook installation

These hooks are staged only. Install them on Oracle as root, run each with a
disposable namespace/standby, and leave `pantry-postgres-oracle-promoter.service`
disabled until the positive fence rehearsal passes.

Copy the scripts and `oracle-promoter.env.example` to
`/usr/local/lib/failover-witness/` and `/etc/failover-witness/`, respectively;
set the Canada key variables in the root-owned env file, then run
`systemctl daemon-reload`. Verify `systemctl is-enabled pantry-postgres-oracle-promoter.service`
still reports `disabled`. The five commands must all be executable and return
non-zero on uncertainty. Do not use the old GCP-only fence command.

The Canada key is the remaining live prerequisite: Oracle needs a dedicated
root-readable private key whose public half is installed in BotAdmin's
`authorized_keys` with a forwarding/fence-only restriction. No key is created
or copied by this change, and no live fence was run.
