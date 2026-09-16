# PantryBot public routing contract

This is the routing contract for the split PantryBot public UI. Cloudflare
Tunnel hostnames are managed outside Git, so this file records the required
entries and the live split. `commands.greeniespantry.uk` is served by the
dedicated active-active commands tunnel
(`c0015a8b-3f9e-4af9-b172-a97b882b4b28`) and `mods.greeniespantry.uk` by the
dedicated active-active mods tunnel (connectors on home and Oracle); the
remaining private/OAuth routes stay on the shared tunnel. The mods cutover
sequence is recorded in [PANTRYBOT-MODS-TUNNEL-PLAN.md](./PANTRYBOT-MODS-TUNNEL-PLAN.md).

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

This hostname is served by the dedicated active-active mods tunnel, mirroring
the commands tunnel pattern: one cloudflared connector on home and one on
Oracle, both pinned to
`cloudflare/cloudflared:2026.7.3@sha256:e39ee8da81ad5e05d77f38d2f51c60ca51bf2a8450ac3abab50c17fdb91d91bf`,
with the tunnel ingress resolving to the local
`pantry-private-site.pantry-bot.svc.cluster.local:3000` in each cluster. DNS is
a proxied CNAME to the tunnel's `cfargotunnel.com` target. The per-site
application tunnels and the route adapter no longer switch this hostname; the
mods tunnel must not contain `oauth`, `commands`, `overlay`, or a wildcard.

The private UI serves browser assets and proxies `/mod/api`, `/login`, and
`/oauth/callback` to `pantry-private-api`. The API origin owns moderator
authentication, session cookies, and mutations; the UI is not an
authentication boundary.

## Operator OAuth

Hostname: `oauth.greeniespantry.uk`

Route:

```text
oauth.greeniespantry.uk/* -> http://auth-authentik-server.auth.svc.cluster.local:80
```

The Cloudflare route now terminates at the Authentik embedded proxy outpost.
Authentik's `PantryBot OAuth Proxy` forwards the authenticated request to
`http://pantry-private-site.pantry-bot.svc.cluster.local:3000`. The provider
is assigned to the embedded outpost and the `PantryBot OAuth Login`
application is explicitly bound to the `Greenie` user. Keep this route
separate from the OAuth-free commands hostname and record any Cloudflare
configuration change in homelab issue #168.

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
In the route adapter inputs it stays OFF (`route_state: standby-excluded` and
absent from `application_routes`) so promotion cannot republish it.

## Canada is last resort, never a failover target

Home and Oracle are always preferred over Canada. Canada is a last-resort
recovery site only: there is no automated controller for it, no systemd
instance is ever installed for it, and live routing is never published to it
during ordinary failover.

- `scripts/pantrybot-promote-site.sh` refuses `PROMOTION_SITE=canada` unless the
  operator action token `CANADA_LAST_RESORT_CONFIRM` is present AND both
  site-dark probes `CANADA_LAST_RESORT_HOME_GATE` and
  `CANADA_LAST_RESORT_ORACLE_GATE` exit 0 (home and Oracle dark). The gates are
  private, host-level probes only; a healthy public route is not proof a site is
  dead (see the "public health is not failover proof" notes above).
- `site_neutral_promoter.py` refuses canada without `--allow-canada-last-resort`
  and refuses canada serve mode entirely (`--once` only). The flag is
  single-pass: it never keeps a lease.
- `observability/failover-witness/publish-cloudflare-routes.sh` refuses a canada
  route publish without the same `CANADA_LAST_RESORT_CONFIRM` token.
- The full procedure is `docs/recovery/runbooks/pantrybot-canada-last-resort.md`;
  restore-normal runs always return routing to home or Oracle.

## Validation

Before enabling the routes, verify:

- `commands.greeniespantry.uk/` returns the static UI without redirecting.
- `commands.greeniespantry.uk/api/public/commands` returns JSON with HTTP 200.
- `commands.greeniespantry.uk/login/*` does not exist and never redirects to OAuth.
- `mods.greeniespantry.uk/mod/` serves the static console shell without API credentials.
- `mods.greeniespantry.uk/mod/api/commands` redirects/authenticates through the API origin.
- `mods.greeniespantry.uk/login/mod` remains reachable through the API origin.
- `oauth.greeniespantry.uk/login/broadcaster` and `/login/bot` reach the
  Authentik login flow before forwarding to PantryBot.
- `overlay.greeniespantry.uk/` returns the overlay shell and WebSocket upgrade
  remains reachable after reconnect — but only after the capacity pass; in this
  pass it remains OFF (`route_state: standby-excluded` in the route adapter
  inputs, absent from `application_routes`).
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
