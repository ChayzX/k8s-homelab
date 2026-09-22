# Canada application metrics relay

Canada publishes the PantryBot API metrics endpoint only on Docker loopback
(`127.0.0.1:13100`). Prometheus in MinecraftMachine cannot scrape that socket
directly, so the relay carries only `/metrics` over a private SSH forward
across Tailscale.

## Deployed design

1. MinecraftMachine runs `pantry-bot-canada-metrics-tunnel.service` as `chase`.
2. The service uses a dedicated Ed25519 key and forwards
   `192.168.40.208:13101` to Canada `127.0.0.1:13100`.
3. Prometheus scrapes only `/metrics` on `192.168.40.208:13101` and adds
   `site=canada` and `role=recovery` labels.
4. The Canada key is restricted to the metrics destination and has no
   interactive, agent, or X11 forwarding.

The existing Canada application tunnel and Docker port mapping already keep the API bound to loopback. No PantryBot container restart is required to create the route; only the tunnel connector's ingress configuration changes.

## Live status

The relay and Prometheus target are enabled. A failed Canada connection causes
the target to go down and the SSH service to retry; it does not expose the API
or database ports publicly.

## Validation gates

- `curl` from the Canada host to `http://127.0.0.1:13100/metrics` returns `200` and contains `pantry_bot_` series.
- Port `192.168.40.208:13101` is reachable only from the private LAN path.
- `/metrics` returns HTTP 200 through the relay.
- Prometheus target `canada-pantry-bot-api` is `up` and `pantry_bot_*{site="canada"}` appears in Prometheus.
- Application `pantry_bot_*` series appear after the active role emits events;
  the current deployed role may have no samples until activity occurs.

## Current state (2026-09-22, #191 / #375)

The relay and its Prometheus targets now exist:

- Home `pantry-bot-canada-metrics-tunnel.service` plus the drop-in `roles.conf` (`failover-witness/home/systemd/pantry-bot-canada-metrics-tunnel-roles.conf`) forward Canada's loopback role ports to Home's LAN IP: api `192.168.40.208:23100`, gateway `:23101`, dispatcher `:23102`, worker `:23103`. The legacy `:13101` forward is also api.
- Home Prometheus jobs `pantry-bot-{api,gateway,dispatcher,worker}-canada` carry `site="canada"`. They are **down while Canada is a standby**, because its apps are stopped. That's expected; dashboards read the serving site, not "all sites up".
- The worker health port is published on Canada as `127.0.0.1:13103`, starting with the next Canada app start.
- Canada logs: `pantrybot-canada-alloy` (`canada/canada-alloy.alloy`, `start-canada-alloy.ps1`) ships container logs to Home Loki with `site="canada"`.
