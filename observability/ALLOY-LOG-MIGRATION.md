# Alloy log migration

Alloy now runs as a per-node DaemonSet on MinecraftMachine and Oracle. It tails Kubernetes pod logs and writes only to self-hosted Loki:

- MinecraftMachine: `http://loki.observability.svc.cluster.local:3100/loki/api/v1/push`
- Oracle: `http://100.84.89.87:31100/loki/api/v1/push`

Alloy is EOL and has been removed from both live clusters. Grafana Cloud log and metrics forwarding remains disabled. The Windows Alloy Cloudflare metrics tunnel is scaled to zero until a self-hosted metrics endpoint is selected.
