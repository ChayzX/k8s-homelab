# Secrets — `observability` namespace

No secret values live in this repo. Every Secret below is created imperatively
by the operator, on the node, before the Deployment that consumes it is
applied. A Deployment whose Secret does not exist yet will sit in
`CreateContainerConfigError` until it does — that is recoverable, not fatal
(same convention as `../pantry-bot/SECRETS.md` and `../jmusicbot/SECRETS.md`).

---

## 1. `opsbot-alert-token` — Bearer token for the `opsbot-dm` contact point

Grafana alerting delivers PantryBot / Twitch alerts through opsbot, not a
Discord webhook: the `opsbot-dm` `webhook` contact point
(`grafana-provisioning.yaml`) POSTs to
`http://opsbot-health.opsbot.svc.cluster.local:9091/alerts/grafana` with
`Authorization: Bearer $OPSBOT_ALERT_TOKEN`. `grafana.yaml` reads that env var
from this Secret (key `token`, **required** — a missing Secret is a loud
`CreateContainerConfigError`, never a contact point quietly getting 401s).

The value is shared with the `opsbot` namespace: a Secret with the same name
and key must hold the same value there. Creation (2026-09-29, value never
printed, written to disk or put on a command line) and rotation are documented
once, in `../opsbot/SECRETS.md` (`opsbot-alert-token`). Short version:

```bash
openssl rand -hex 32 | tr -d '\n' \
  | kubectl -n opsbot create secret generic opsbot-alert-token --from-file=token=/dev/stdin
kubectl -n opsbot get secret opsbot-alert-token -o jsonpath='{.data.token}' | base64 -d \
  | kubectl -n observability create secret generic opsbot-alert-token --from-file=token=/dev/stdin
```

After rotating, restart opsbot first, then Grafana — alerting provisioning
(and the env var) are read once, at startup.

**Retired: `grafana-discord-webhooks`.** The previous Discord-webhook contact
point (`discord-pantry-twitch-dm`, env `PANTY_TWITCH_DISCORD_WEBHOOK_URL`)
needed a webhook that was never created and will not be. Do not create that
Secret; nothing reads it any more.

---

## 2. `grafana-admin` — optional admin password (pre-existing)

Already documented in `grafana.yaml` (the `GF_SECURITY_ADMIN_PASSWORD`
`secretKeyRef` marked `optional: true`). Create it only if the admin password
needs resetting:

```bash
kubectl -n observability create secret generic grafana-admin \
  --from-literal=admin-password='...'
```

---

## 3. and 4. Retired: `grafana-cloud-loki` and `grafana-cloud-metrics`

Historical: these Secrets were the Grafana Cloud Loki push credential and
Prometheus remote-write credential. Grafana Cloud is no longer used, no live
manifest consumes either Secret, and observability is self-hosted Grafana,
Prometheus and Loki in this namespace. Do not create them. They were deleted
from both clusters on 2026-09-29 after confirming no pod referenced them.
The numbering of the sections below is unchanged so existing references
still resolve.

---

## 5. `mcp-grafana-grafana-token` — Grafana service account token for mcp-grafana

Consumed by `mcp-grafana.yaml`'s `GRAFANA_SERVICE_ACCOUNT_TOKEN` env var. Create
a service account + token in the self-hosted Grafana (Administration ->
Service accounts), scoped to read-only roles sufficient for the MCP tools you
intend to use (dashboards/datasources/query at minimum), then:

```bash
kubectl -n observability create secret generic mcp-grafana-grafana-token \
  --from-literal=token='<glsa_... service account token>'
```

The key must be exactly `token`. A missing Secret sits the pod in
`CreateContainerConfigError`, same as every other required Secret in this
namespace.

## 6. `mcp-grafana-server-token` — mcp-grafana's own caller-auth bearer token

Consumed as `MCP_GRAFANA_SERVER_TOKEN`. This is a second, independent access
check inside mcp-grafana itself, on top of whatever network-level restriction
guards `mcp-grafana.greeniespantry.uk` (see `MCP-GRAFANA-SETUP.md`). Generate
a random value -- this is not a Grafana credential, just a shared secret
between this server and whatever MCP client (Claude) calls it:

```bash
kubectl -n observability create secret generic mcp-grafana-server-token \
  --from-literal=token="$(openssl rand -hex 32)"
```

Save the generated value somewhere you can paste it into the MCP client's
connector config (as its Bearer token) -- `kubectl` will not show it back to
you unencoded.

## 7. `mcp-grafana-cloudflared-tunnel-token` — dedicated tunnel token for the mcp-grafana connector

Same shape as `ci-tunnel-token` / `commands-cloudflared-tunnel-token`: a
brand-new Cloudflare Tunnel created for this hostname only (do not reuse an
existing tunnel -- see `mcp-grafana-cloudflared.yaml`'s header for why).

```bash
kubectl -n observability create secret generic mcp-grafana-cloudflared-tunnel-token \
  --from-literal=TUNNEL_TOKEN='<paste the token from the Cloudflare dashboard>'
```

Full dashboard-side setup (tunnel, public hostname, Cloudflare Access policy)
is in `MCP-GRAFANA-SETUP.md` -- it can't be scripted from a repo-only change,
same caveat as `ci-tunnel/MANUAL-SETUP.md`.
