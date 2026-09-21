# Secrets — `observability` namespace

No secret values live in this repo. Every Secret below is created imperatively
by the operator, on the node, before the Deployment that consumes it is
applied. A Deployment whose Secret does not exist yet will sit in
`CreateContainerConfigError` until it does — that is recoverable, not fatal
(same convention as `../pantry-bot/SECRETS.md` and `../jmusicbot/SECRETS.md`).

---

## 1. `grafana-discord-webhooks` — Discord webhook for Pantry-bot / Twitch alerting

The alerting ConfigMap still contains the Discord contact-point definition,
but it is currently not mounted by the Grafana Deployment because the Secret
was absent during the Grafana Cloud migration. Existing alert state remains
in Grafana's database; this Secret is needed before re-enabling that
provisioner.

Do not create a placeholder value. An empty or invalid webhook makes Grafana's
alerting provisioner fail at startup.

```bash
kubectl -n observability create secret generic grafana-discord-webhooks \
  --from-literal=pantry-twitch-webhook-url='https://discord.com/api/webhooks/REPLACE/ME'
```

The key must be exactly `pantry-twitch-webhook-url` — that is the name
`grafana.yaml` reads. A typo produces the same `CreateContainerConfigError`.

### What the webhook URL must be

The provisioned contact point posts to the channel the webhook was created in,
and its message is prefixed with `<@204282471506771971>` so that user is pinged
(Discord pings for mentions in the main message content, which is why
`use_embed_description` is deliberately left unset). Two ways to satisfy that:

- **Recommended:** a webhook in a private channel of a Discord server both
  users can see. The mention pings user 204282471506771971 on every alert.
- A webhook created in a DM / group-DM with user 204282471506771971 (Discord
  supports webhooks in DMs). The mention is then redundant but harmless.

A Discord webhook URL is a bearer credential — anyone holding it can post to
that channel as the webhook. Treat it like a token.

### Updating it later

```bash
kubectl -n observability create secret generic grafana-discord-webhooks \
  --from-literal=pantry-twitch-webhook-url='https://discord.com/api/webhooks/REPLACE/ME' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n observability rollout restart deploy/grafana
```

The `rollout restart` is **required**: alerting provisioning files are read
once, at Grafana startup — a changed Secret value is not picked up until the
pod restarts.

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

## 3. `grafana-cloud-loki` — Grafana Cloud Loki push credential (alloy)

Consumed by the alloy DaemonSet (`alloy-logs-home.yaml` / `alloy-logs-oracle.yaml`) as a mounted file.

**This Secret MUST exist before `alloy-logs-*.yaml` is applied**, or the pod sits
in `CreateContainerConfigError` (the volume references it directly, not
`optional: true`).

```bash
kubectl -n observability create secret generic grafana-cloud-loki \
  --from-literal=grafana-cloud-loki-password='<the glc_... access token>'
```

The key must be exactly `grafana-cloud-loki-password` — that's the filename
alloy's config expects under the mount. Username (`1769810`) and the push
URL (`https://logs-prod-036.grafana.net/loki/api/v1/push`) are not secret and
are configured in the alloy DaemonSet manifests.

The live Alloy manifests currently write only to the local `loki` service.
Enabling Grafana Cloud as a second destination requires a separate reviewed
change and a receipt/freshness check; do not infer that forwarding is active
from this Secret contract alone.

---

Never commit this Secret or paste its YAML. When the real webhook is restored,
the alerting mount and environment variable must be re-enabled together.

## 4. `grafana-cloud-metrics` — Grafana Cloud Prometheus remote-write credentials

This Secret is consumed by both the home Prometheus Deployment and the
Oracle-specific `prometheus-oracle` Deployment. The same Cloud token is
mounted independently in each cluster; it is never committed to the repo.

```bash
kubectl -n observability create secret generic grafana-cloud-metrics \
  --from-literal=password='<metrics:write access-policy-token>'
```

The current manifests require the `password` key; the documented Secret
contract contains only that remote-write credential. Use a token with only the
`metrics:write` scope. The remote-write URL and username are
configured in the tracked Prometheus ConfigMaps, not read from this Secret.
This Secret is for Prometheus remote write only. It does not contain, and must
not be used as, the separate `metrics:read` credentials used to query Grafana
Cloud.

For query access, use the read credentials and query endpoint supplied by the
Grafana Cloud portal for the stack's Prometheus data source. The query
endpoint is portal-provided; do not derive it from the remote-write URL or
from `/api/prom/push`. That path is the write destination configured for
Prometheus in this repository.

See
`docs/recovery/GRAFANA-CLOUD-MIGRATION.md` for the staged cutover gates.

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
