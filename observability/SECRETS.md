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

Never commit this Secret or paste its YAML. When the real webhook is restored,
the alerting mount and environment variable must be re-enabled together.

## 4. `grafana-cloud-metrics` — Grafana Cloud Prometheus remote-write token

This Secret is consumed by the home Prometheus remote-write configuration.
Oracle does not currently have a corresponding active collector Deployment.

```bash
kubectl -n observability create secret generic grafana-cloud-metrics \
  --from-literal=remote-write-url='https://prometheus-prod-<region>.grafana.net/api/prom/push' \
  --from-literal=username='<metrics-instance-id>' \
  --from-literal=password='<metrics:write access-policy-token>'
```

The keys must be exactly `remote-write-url`, `username`, and `password`; use a
token with only the `metrics:write` scope. See
`docs/recovery/GRAFANA-CLOUD-MIGRATION.md` for the staged cutover gates.
