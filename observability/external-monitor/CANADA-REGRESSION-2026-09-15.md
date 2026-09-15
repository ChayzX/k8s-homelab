# Canada PantryBot Regression — 2026-09-15

Scope: read-only audit of the active Canada site. No service restart, fence, promotion, or configuration mutation was performed.

## Results

| Check | Result | Evidence |
|---|---|---|
| Canada containers | PASS | `docker ps` showed 8 running containers: public, private, overlay, dispatcher, worker, gateway, api, postgres. All reported Up ~7–8 hours. |
| PostgreSQL role | PASS | `pg_is_in_recovery()` returned `f` using database `pantry`; Canada is currently writable primary. |
| API readiness | PASS | `127.0.0.1:13100/ready` returned HTTP 200. |
| Public readiness | PASS | `127.0.0.1:18080/ready` returned HTTP 200. |
| Private readiness | PASS | `127.0.0.1:18081/ready` returned HTTP 200. |
| Overlay readiness | PASS | `127.0.0.1:18082/readyz` returned HTTP 200. |
| Gateway/dispatcher route | OBSERVED | `/ready` returned HTTP 404 on ports 13101 and 13102; processes are running. These services do not expose that route in the current image. |
| Commands URL | PASS | `commands.greeniespantry.uk` returned Canada origin marker; `/commands.html` redirects to `/` (308), reachable. |
| Overlay public route | PASS | `overlay.greeniespantry.uk` returned 302 to `/catches-ticker.html`; route is reachable. |
| Mods public route | OBSERVED | `mods.greeniespantry.uk` returned HTTP 404 from reachable origin; route is present but root path is not defined by the current app. |
| Private metrics relay | PASS | Prometheus target `canada-pantry-bot-api` at `192.168.40.208:13101/metrics` is `health=up`, empty `lastError`, labels `site=canada, role=recovery`. |
| Duplicate Oracle origins | PASS (configuration evidence) | Current route input marks Oracle hostnames empty / standby-excluded; live Prometheus evidence also confirms Canada relay is active. A fresh Cloudflare API inventory was not performed in this read-only pass. |

## Findings

- Canada is healthy enough to serve the public/private/API roles and is the current PostgreSQL primary.
- Gateway and dispatcher need explicit health endpoints if the regression contract requires HTTP readiness; this is a known image behavior, not a restart failure.
- The mods root returning 404 should be tested against its intended path before treating it as an application failure.
- The Kubernetes home API scrape target is down while home HA replicas are intentionally inactive; the Canada relay is the valid active metrics source.

## Reproduction commands

Commands were executed over SSH to `BotAdmin@100.104.83.28` and via MinecraftMachine Prometheus API. Secrets were not recorded.
