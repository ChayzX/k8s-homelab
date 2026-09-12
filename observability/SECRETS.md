# Secrets — `observability` namespace

No secret values live in this repo. Every Secret below is created imperatively
by the operator, on the node, before the Deployment that consumes it is
applied. A Deployment whose Secret does not exist yet will sit in
`CreateContainerConfigError` until it does — that is recoverable, not fatal
(same convention as `../pantry-bot/SECRETS.md` and `../jmusicbot/SECRETS.md`).

---

## 1. `grafana-discord-webhooks` — Discord webhook for Pantry-bot / Twitch alerting

Consumed by the grafana Deployment as the env var
`PANTY_TWITCH_DISCORD_WEBHOOK_URL`, which the provisioned alerting contact
point (`grafana-provisioning-alerting` ConfigMap → `alerting.yaml`, in
`grafana-provisioning.yaml`) interpolates into its Discord webhook URL.

**This Secret MUST exist before `grafana.yaml` is applied.** It is a required
`secretKeyRef`, deliberately not `optional: true`: if the env var is unset the
provisioning interpolation yields an empty webhook URL, the Discord contact
point fails its provisioning validation and Grafana refuses to start. A missing
Secret instead fails loudly as `CreateContainerConfigError` before anything
alerting-related can break silently.

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

## 3. `grafana-cloud-loki` — Grafana Cloud Loki push credential (promtail)

Consumed by the promtail DaemonSet (`promtail.yaml`) as a mounted file, and
referenced from `promtail-config.yaml`'s second `clients` entry via
`password_file: /etc/promtail/secrets/grafana-cloud-loki-password` — kept out
of the ConfigMap so the credential never sits in plaintext config.

**This Secret MUST exist before `promtail.yaml` is applied**, or the pod sits
in `CreateContainerConfigError` (the volume references it directly, not
`optional: true`).

```bash
kubectl -n observability create secret generic grafana-cloud-loki \
  --from-literal=grafana-cloud-loki-password='<the glc_... access token>'
```

The key must be exactly `grafana-cloud-loki-password` — that's the filename
promtail's config expects under the mount. Username (`1769810`) and the push
URL (`https://logs-prod-036.grafana.net/loki/api/v1/push`) are not secret and
are already in `promtail-config.yaml` directly.

This is currently a **dual-write**: promtail sends to both the local `loki`
service and Grafana Cloud. Once Grafana Cloud is confirmed receiving data
(check `{job=~".+"}` in Grafana Cloud's Explore, or the local Grafana's Loki
datasource repointed at `logs-prod-036.grafana.net`), the local `loki` client
entry can be removed and the in-cluster Loki Deployment decommissioned to
actually free its RAM and stop the HDD-compaction latency noted in
`promtail-config.yaml`. That cutover is a separate, deliberate follow-up, not
done here.

---

## 4. `grafana-cloud-metrics` — Grafana Cloud Prometheus push credential

Consumed by each site-local Prometheus as the remote-write credential. The
Secret must contain exactly `remote-write-url`, `username`, and `password`:

```bash
kubectl -n observability create secret generic grafana-cloud-metrics \
  --from-literal=remote-write-url='https://prometheus-prod-XX.grafana.net/api/prom/push' \
  --from-literal=username='<metrics instance ID>' \
  --from-literal=password='<metrics:write access-policy token>'
```

This is a **push-only** credential. It is intentionally not expected to
authenticate Grafana Cloud's query API; verifying dashboards in Explore
requires logging into Grafana Cloud or provisioning a separate credential
with the appropriate `metrics:read` scope. Never replace this Secret with a
migration or UI token without first proving that it retains `metrics:write`
and checking the local Prometheus remote-write failure counter.

---

## Checklist before applying the Deployments

```bash
kubectl -n observability get secret grafana-discord-webhooks
```

Must exist before `grafana.yaml` is applied. Never commit it, never
`kubectl get -o yaml` it into a paste.
