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

## Validation

Before enabling the routes, verify:

- `commands.greeniespantry.uk/` returns the static UI without redirecting.
- `commands.greeniespantry.uk/api/public/commands` returns JSON with HTTP 200.
- `commands.greeniespantry.uk/login/*` does not exist and never redirects to OAuth.
- `mods.greeniespantry.uk/mod/` serves the static console shell without API credentials.
- `mods.greeniespantry.uk/mod/api/commands` redirects/authenticates through the API origin.
- `mods.greeniespantry.uk/login/mod` remains reachable through the API origin.
- `oauth.greeniespantry.uk/login/broadcaster` and `/login/bot` remain reachable.
- Both home and Oracle origins expose equivalent routes before automatic failover is enabled.
