# Off-LAN host metrics

Use `alloy-linux.alloy.example` for a Unix PC or
`alloy-windows.alloy.example` for Windows 11 when the host cannot be reached
from the homelab LAN. Alloy scrapes the local host and sends a deliberately small,
allowlisted set of metrics outbound to Grafana Cloud over TLS. No inbound
port-forward, Cloudflare route, VPN, or Prometheus exposure is required.

## Setup

1. Install the current Grafana Alloy package using Grafana's platform-specific
   installation instructions. Windows 11 uses the Windows exporter component;
   it emits `windows_*` metric names and therefore uses the Windows template.
2. Copy the example configuration to Alloy's config path.
3. Create the token file containing only the Grafana Cloud `metrics:write`
   token. On Linux:

   ```bash
   sudo install -o alloy -g alloy -m 600 /dev/null /etc/alloy/grafana-cloud-token
   sudoedit /etc/alloy/grafana-cloud-token
   ```

   On Windows, create
   `C:\ProgramData\GrafanaLabs\Alloy\grafana-cloud-token` and grant read
   access only to the Alloy service account. Do not put the token in the
   Alloy configuration or a command-line argument.

4. Validate the config with `alloy run --stability.level=generally-available
   /etc/alloy/config.alloy` (use the service's normal validation command if
   the package provides one), then restart Alloy.
5. In Grafana Cloud, query `up{job="host",site="remote"}` to confirm receipt.
   For Linux, also query `node_memory_MemAvailable_bytes{site="remote"}`;
   Windows uses `windows_memory_available_bytes{site="remote"}`.

The stack ID (`3548270`) and remote-write URL are intentionally non-secret;
the token is never committed. The allowlist is limited to core CPU, memory,
load, filesystem, network, uptime, and exporter identity families. It omits
per-process, container, textfile, service, and other high-cardinality
collectors to protect the free-tier budget. Windows exporter metric names and
collectors are documented by the upstream project.
