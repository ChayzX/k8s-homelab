# Secrets — `pantry-bot` namespace

No secret values live in this repo. Create these imperatively on the node,
**before** applying the Deployments.

Four Secrets are needed here (a fifth is optional). Deploy pipeline is GitHub Actions
(`.github/workflows/deploy.yml` in the pantry-bot repo — a single
"PantryBot CI/CD" workflow that builds, applies, restarts, and verifies
automatically on every merge to `main`; a merge is the approval, not a
separate manual click) — GitHub Actions secrets
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

## 4. `pantry-bot-litestream` — R2 credentials for continuous SQLite backup

New as of the litestream sidecar (`25-configmap-litestream.yaml`,
`40-deployment.yaml`) — Step A of the cross-node failover migration, see
that ConfigMap's header comment. Without this Secret the pod fails to start
(`CreateContainerConfigError`), by design — the sidecar has nothing useful
to do without R2 credentials, so failing loudly beats silently not backing
up.

```bash
kubectl -n pantry-bot create secret generic pantry-bot-litestream \
  --from-literal=LITESTREAM_ACCESS_KEY_ID='REPLACE_ME' \
  --from-literal=LITESTREAM_SECRET_ACCESS_KEY='REPLACE_ME'
```

Use an R2 API token scoped only to the backup bucket/path — don't reuse the
Terraform-state token from `pantry-bot-infra` for this; different blast
radius. Also edit `25-configmap-litestream.yaml`'s `REPLACE_ME_R2_BUCKET` /
`REPLACE_ME_R2_ENDPOINT` before applying.

Before trusting this backup: after it's been running a while, actually test
a restore (`litestream restore` against the R2 path, into a scratch file,
and open it) rather than assuming replication working means restoration
works. This is the exact gap Step B's PVC cutover depends on being closed
first.

---

## 5. `pantry-bot-discord-alerts` — optional operational alert webhook

Not required — `envFrom` references it with `optional: true`, and
`src/discordAlert.ts` no-ops gracefully (with a console log) if unset. Create
it only if you want fatal errors, lost Twitch/EventSub connections, and
broken channel-point setup posted to a Discord channel (typically a second
server's mod/ops channel — separate from k3s-watcher's personal DM alerts,
which cover cluster/pod health, not bot-internal application state).

```bash
kubectl -n pantry-bot create secret generic pantry-bot-discord-alerts \
  --from-literal=DISCORD_ALERT_WEBHOOK_URL='https://discord.com/api/webhooks/REPLACE/ME'
```

Key must be exactly `DISCORD_ALERT_WEBHOOK_URL` — that's what `discordAlert.ts`
reads. Create the webhook in the target channel's settings (Integrations ->
Webhooks -> New Webhook -> Copy URL) in whichever Discord server should
receive these.

---

## Verify the pipeline actually works — do not assume

Without `ghcr-pull-secret`: pod stuck in `ImagePullBackOff` -- loud and
obvious, the kubelet needs it to pull the private image.

Force a real end-to-end test rather than trusting it unattended: push to
`main` — the consolidated `deploy.yml` builds, applies, restarts, and
verifies automatically, no second click needed — and confirm the pod's age
resets:

```bash
kubectl -n pantry-bot get pods -w
```

---

## Checklist before applying the Deployments

```bash
kubectl -n pantry-bot get secret pantry-bot-twitch cloudflared-tunnel ghcr-pull-secret pantry-bot-litestream
```

All four must exist (`pantry-bot-discord-alerts` is optional). Never commit
them, never `kubectl get -o yaml` them into a paste.
