# PantryBot public routing contract

This is the routing contract for the split PantryBot public UI. Cloudflare
Tunnel hostnames are currently managed outside Git, so this file records the
required entries before the split deployment is enabled.

## Viewer command guide

Hostname: `commands.greeniespantry.uk`

Route order must be more specific before the catch-all route:

1. `commands.greeniespantry.uk/api/public/commands` ->
   `http://pantry-commands-site.pantry-bot.svc:3000/api/public/commands`
2. `commands.greeniespantry.uk/*` ->
   `http://pantry-commands-site.pantry-bot.svc:3000`

Both routes are read-only and unauthenticated. The commands site owns the
credential-free command manifest; it receives no Twitch, OAuth, or database
secrets.

## Moderator console

Hostname: `mods.greeniespantry.uk`

Route:

```text
mods.greeniespantry.uk/* -> http://pantry-private-site.pantry-bot.svc:3000
```

The private UI serves browser assets and proxies `/mod/api`, `/login`, and
`/oauth/callback` to `pantry-private-api`. The API origin owns moderator
authentication, session cookies, and mutations; the UI is not an
authentication boundary.

## Operator OAuth

Hostname: `oauth.greeniespantry.uk`

Route:

```text
oauth.greeniespantry.uk/* -> http://pantry-bot.pantry-bot.svc:3000
```

The public commands hostname must not route to this OAuth hostname, and the
static commands/private UI pods must not receive OAuth, Twitch, or database
credentials.

## OBS overlay delivery

Hostname: `overlay.greeniespantry.uk`

Route:

```text
overlay.greeniespantry.uk/* -> http://pantry-overlay.pantry-bot.svc:8080
```

This origin serves the static browser-source assets and WebSocket upgrade on
the same hostname. It is backed by PostgreSQL reconnect snapshots and the
fenced overlay outbox lane; the Cloudflare route must not be enabled until
equivalent home and Oracle overlay capacity and WebSocket reconnect tests pass.

## Validation

Before enabling the routes, verify:

- `commands.greeniespantry.uk/` returns the static UI without redirecting.
- `commands.greeniespantry.uk/api/public/commands` returns JSON with HTTP 200.
- `commands.greeniespantry.uk/login/*` does not exist and never redirects to OAuth.
- `mods.greeniespantry.uk/mod/` serves the static console shell without API credentials.
- `mods.greeniespantry.uk/mod/api/commands` redirects/authenticates through the API origin.
- `mods.greeniespantry.uk/login/mod` remains reachable through the API origin.
- `oauth.greeniespantry.uk/login/broadcaster` and `/login/bot` remain reachable.
- `overlay.greeniespantry.uk/` returns the overlay shell and WebSocket upgrade
  remains reachable after reconnect.
- Both home and Oracle origins expose equivalent routes before automatic failover is enabled.

## Free routing proof harness

The repository includes a read-only proof harness at
`observability/external-monitor/routing_proof.py`. It is intended to run from
an external observer or from a maintenance workstation; it does not use
Kubernetes credentials and does not change DNS, Cloudflare, tunnels, or live
services.

Run it with direct, independently reachable origin URLs and the friendly
hostname:

```bash
python3 observability/external-monitor/routing_proof.py \
  --home-origin https://<home-origin> \
  --oracle-origin https://<oracle-origin> \
  --public-url https://commands.greeniespantry.uk \
  --output /tmp/pantrybot-routing-proof.json \
  --detection-assumption-seconds 60 \
  --convergence-assumption-seconds 90
```

The harness probes `/` and `/api/public/commands` separately for home, Oracle,
and the public hostname. Each probe records its URL, status, content type,
UTC start/end timestamps, and elapsed time. The report also records the
detection and route-convergence assumptions; these are planning assumptions,
not measured failover times. A failed direct origin is reported under its own
origin and is not hidden by a healthy other origin.

An optional origin marker can verify which site the currently observed public
route reaches, if the externally exposed response includes a deliberate
non-secret marker header:

```bash
python3 observability/external-monitor/routing_proof.py \
  --home-origin https://<home-origin> \
  --oracle-origin https://<oracle-origin> \
  --public-url https://commands.greeniespantry.uk \
  --public-origin-header X-PantryBot-Origin \
  --expected-public-origin home
```

This verifies only the observed current target. A healthy public response or a
single target marker must never be described as Cloudflare failover. The
report therefore emits `cloudflare_failover.status=not_verified` unless an
external before/after route artifact is explicitly supplied with both targets:

```json
{
  "public_hostname": "commands.greeniespantry.uk",
  "observations": [
    {"observed_at": "2026-09-10T20:00:00Z", "target": "home", "externally_observed": true},
    {"observed_at": "2026-09-10T20:05:00Z", "target": "oracle", "externally_observed": true}
  ]
}
```

Pass that artifact with `--transition-evidence <file>` only after the route
target was observed externally. The harness validates that the hostname
matches, both `home` and `oracle` were observed, timestamps are parseable, and
each observation is explicitly external. Even with this artifact, the direct
origin health results remain separate from the route-transition evidence.

The proof output is evidence for issue tracking, not a Cloudflare control
plane change. It must not be used to claim automatic failover until an
externally observed before/after transition and measured convergence result
are attached.
