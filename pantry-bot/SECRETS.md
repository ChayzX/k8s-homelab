# Secrets — `pantry-bot` namespace

No secret values live in this repo. Create these imperatively on the node,
**before** applying the Deployments.

Three Secrets are needed here. Deploy pipeline is GitHub Actions
(`.github/workflows/publish.yml` + `deploy.yml` in the pantry-bot repo,
manual-click `workflow_dispatch` for deploy) — GitHub Actions secrets
(`CF_ACCESS_CLIENT_ID`/`SECRET`, `KUBE_CONFIG_PANTRYBOT`) live in the GitHub
repo settings, not here; this file only covers Secrets applied to the
cluster. Keel (previously the deploy mechanism, retired 2026-08-13) is gone
— see `ARCHITECTURE.md`'s CI/CD note.

---

## 1. `pantry-bot-twitch` — Twitch application credentials

Currently in `/home/chase/Downloads/pantry-bot/.env` as `TWITCH_CLIENT_ID` and
`TWITCH_CLIENT_SECRET`. The third Twitch key in that file,
`TWITCH_OAUTH_REDIRECT_URI`, is a public URL and lives in the ConfigMap instead.

```bash
kubectl -n pantry-bot create secret generic pantry-bot-twitch \
  --from-literal=TWITCH_CLIENT_ID='REPLACE_ME' \
  --from-literal=TWITCH_CLIENT_SECRET='REPLACE_ME'
```

The key names must match exactly — the Deployment consumes this Secret with
`envFrom.secretRef`, so each key becomes an environment variable of the same
name. A typo produces a bot that starts and then fails Twitch auth at runtime,
not a pod that fails to start.

Verify the key names (never the values):

```bash
kubectl -n pantry-bot get secret pantry-bot-twitch -o jsonpath='{.data}' | tr ',' '\n'
```

---

## 2. `cloudflared-tunnel` — Cloudflare Tunnel token

Currently `CLOUDFLARE_TUNNEL_TOKEN` in the same `.env`, passed on the Compose
command line. Here it is an env var sourced from a Secret, so it no longer
appears in the process table or in `kubectl describe pod`.

```bash
kubectl -n pantry-bot create secret generic cloudflared-tunnel \
  --from-literal=TUNNEL_TOKEN='REPLACE_ME'
```

The key must be `TUNNEL_TOKEN` — that is the environment variable name
cloudflared itself reads.

This token is high value: it identifies the tunnel *and* authorises running it.
Anyone holding it can stand up a connector for `oauth.greeniespantry.uk`. If it
is ever exposed, rotate it in the Cloudflare Zero Trust dashboard
(Networks > Tunnels > this tunnel > Refresh token) and recreate this Secret.

---

## 3. `ghcr-pull-secret` — private GHCR access

`ghcr.io/chayzx/pantry-bot` is a **private** package. Verified: an anonymous
GHCR manifest request returns HTTP 403.

```bash
kubectl -n pantry-bot create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=chayzx \
  --docker-password='<GITHUB_PAT>' \
  --docker-email=chase@example.invalid
```

The PAT needs the **`read:packages`** scope. A classic PAT with only `repo`
scope authenticates successfully against GitHub and still gets 403 from the
registry — which surfaces as `ImagePullBackOff` with a 403, not as an auth
error, and sends people looking in the wrong place.

Test the PAT before creating the Secret:

```bash
T=$(curl -s -u chayzx:<GITHUB_PAT> \
  'https://ghcr.io/token?scope=repository:chayzx/pantry-bot:pull&service=ghcr.io' \
  | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" \
  https://ghcr.io/v2/chayzx/pantry-bot/manifests/latest
# 200 = good. 403 = the PAT is missing read:packages.
```

### Alternative: reuse the existing docker login

This host already ran `docker login ghcr.io` (Watchtower mounts
`~/.docker/config.json` for exactly this). You can convert it directly:

```bash
kubectl -n pantry-bot create secret generic ghcr-pull-secret \
  --from-file=.dockerconfigjson=$HOME/.docker/config.json \
  --type=kubernetes.io/dockerconfigjson
```

Check first that the file contains a real base64 `auth` entry for `ghcr.io` and
not a `credsStore`/`credHelpers` reference — if Docker delegated the credential
to a helper (`"credsStore": "desktop"`), the file holds no usable secret and the
resulting pull Secret will silently be empty. In that case use the explicit
`create secret docker-registry` form above.

---

## 4. `pantry-bot-discord-alerts` — optional operational alerts (Discord bot DM)

Not required — `envFrom` references it with `optional: true`, and
`src/discordAlert.ts` no-ops gracefully (with a console log) if unset. Create
it only if you want fatal errors, lost Twitch chat/EventSub connections, and
broken channel-point setup DMed to you or your mods — bot-internal
application state, distinct from k3s-watcher's cluster/pod health alerts.

Reuses the **same Discord bot** (bot token) that `observability/k3s-watcher`
already DMs alerts with — no separate webhook. See
`observability/k3s-watcher/watcher.env`'s `DISCORD_BOT_TOKEN` on the host for
the existing token.

```bash
kubectl -n pantry-bot create secret generic pantry-bot-discord-alerts \
  --from-literal=DISCORD_BOT_TOKEN='<same token as k3s-watcher DISCORD_BOT_TOKEN>' \
  --from-literal=DISCORD_ALERT_USER_IDS='<comma-separated Discord user IDs to DM>'
```

Keys must be exactly `DISCORD_BOT_TOKEN` and `DISCORD_ALERT_USER_IDS` — that's
what `discordAlert.ts` reads. `DISCORD_ALERT_USER_IDS` accepts one or more
IDs (comma-separated) so both the homelab owner and the streamer/mods can be
DMed independently of k3s-watcher's own recipient list.

---

## Verify the pipeline actually works — do not assume

Without `ghcr-pull-secret`: pod stuck in `ImagePullBackOff` -- loud and
obvious, the kubelet needs it to pull the private image.

Force a real end-to-end test rather than trusting it unattended: push to
`main` (auto-builds via `publish.yml`), then manually trigger `deploy.yml`
(Actions tab -> Run workflow) and confirm the pod's age resets:

```bash
kubectl -n pantry-bot get pods -w
```

---

## Checklist before applying the Deployments

```bash
kubectl -n pantry-bot get secret pantry-bot-twitch cloudflared-tunnel ghcr-pull-secret
```

All three must exist. Never commit them, never `kubectl get -o yaml` them into
a paste.
