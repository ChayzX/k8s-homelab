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

This is currently a **dual-write**: alloy sends to both the local `loki`
service and Grafana Cloud. Once Grafana Cloud is confirmed receiving data
(check `{job=~".+"}` in Grafana Cloud's Explore, or the local Grafana's Loki
datasource repointed at `logs-prod-036.grafana.net`), the local `loki` client
entry can be removed and the in-cluster Loki Deployment decommissioned to
actually free its RAM and stop the HDD-compaction latency noted in
the previous collector configuration. That cutover is a separate, deliberate follow-up, not
done here.

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
