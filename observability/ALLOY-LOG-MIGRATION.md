# Alloy log migration

Alloy now runs as a per-node DaemonSet on MinecraftMachine and Oracle. It tails Kubernetes pod logs and writes only to self-hosted Loki:

- MinecraftMachine: `http://loki.observability.svc.cluster.local:3100/loki/api/v1/push`
- Oracle: `http://100.84.89.87:31100/loki/api/v1/push`

The previous log collector is retired and has been removed from both live
clusters. Alloy is the active collector and currently writes only to self-hosted Loki; Grafana Cloud
log and metrics forwarding remains disabled. The Windows Alloy Cloudflare
metrics tunnel is scaled to zero until a self-hosted metrics endpoint is
selected.

Both site pipelines drop lines containing access/refresh tokens or bearer
authorization material before forwarding. This is defense in depth; application
code must still avoid logging credentials.
