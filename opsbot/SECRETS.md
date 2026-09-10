# Secrets — `opsbot` namespace

No secret values live in this repo. Create the Secrets below imperatively,
**before** applying `40-deployment.yaml`. This mirrors every other bot in
this repo — see `../pantry-bot/SECRETS.md`, `../minecraft/secrets.md`.

---

## `opsbot-discord` — bot token + authorization allowlist

Two values, one Secret:

| Key | Meaning |
|---|---|
| `DISCORD_BOT_TOKEN` | The bot token from the Discord Developer Portal application. |
| `DISCORD_USER_ID` | The single Discord user ID allowed to invoke commands. Reuse the existing convention used by `scripts/minecraft_backup.py` and `scripts/minecraft_exporter.py`. |

```bash
kubectl -n opsbot create secret generic opsbot-discord \
  --from-literal=DISCORD_BOT_TOKEN='REPLACE_WITH_VALUE' \
  --from-literal=DISCORD_USER_ID='REPLACE_WITH_VALUE'
```

The key names must match exactly — the Deployment consumes this Secret via
individual `secretKeyRef` entries (not `envFrom`), so each key becomes the
named environment variable the bot code (`opsbot/bot/`) reads at startup.

Verify the key names (never the values):

```bash
kubectl -n opsbot get secret opsbot-discord -o jsonpath='{.data}' | tr ',' '\n'
```

**Do not create this Secret yet if the Discord application doesn't exist
yet** — `40-deployment.yaml` references it by
name and will sit in `CreateContainerConfigError` until it exists, which is
the intended, loud failure mode (same pattern as `../minecraft/minecraft.yaml`
before its Secret is created).

---

## `opsbot-witness` — site-scoped ownership/fencing

This Secret is mandatory for the ownership gate. Opsbot acquires a lease from
the neutral failover witness before `bot.run()` starts the Discord gateway.
The same lease is renewed while running; a rejected or failed renewal closes
the gateway and all protected Kubernetes/RCON calls fail closed. The witness
must be reachable from the pod and must be shared by the home and Oracle
deployments. Never reuse the Discord or GitHub token here.

| Key | Meaning |
|---|---|
| `OPSBOT_SITE` | Exactly `home` or `oracle`; identifies this deployment's site. |
| `OPSBOT_WITNESS_URL` | Base URL for the witness, including scheme and port if needed. |
| `OPSBOT_WITNESS_SECRET` | Shared Bearer secret accepted by the witness. |

Example (replace every value; do not commit it):

```bash
kubectl -n opsbot create secret generic opsbot-witness \
  --from-literal=OPSBOT_SITE='home' \
  --from-literal=OPSBOT_WITNESS_URL='https://witness.example.invalid' \
  --from-literal=OPSBOT_WITNESS_SECRET='REPLACE_WITH_SHARED_SECRET'
```

The witness contract is `POST /v1/authority/acquire` and
`POST /v1/authority/renew`, authenticated with `Authorization: Bearer ...`.
Acquire returns `{site, epoch, expires_at, token}`; a 409 or any renewal
failure fences Opsbot. The bot accepts only a lease whose returned `site`
matches `OPSBOT_SITE`.

## `ghcr-pull-secret` — private GHCR access

`ghcr.io/chayzx/opsbot` is a **private** package, same as pantry-bot's. This
Secret is what lets the kubelet pull it; `40-deployment.yaml` already
references it via `imagePullSecrets`. **Creating it is being handled
separately, not part of this doc's task** — this section only documents how,
mirroring `../pantry-bot/SECRETS.md`.

```bash
kubectl -n opsbot create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=chayzx \
  --docker-password='<GITHUB_PAT>' \
  --docker-email=chase@example.invalid
```

The PAT needs the **`read:packages`** scope. A classic PAT with only `repo`
scope authenticates fine against GitHub and still gets 403 from the
registry — surfaces as `ImagePullBackOff`, not an auth error.

### Alternative: reuse the existing docker login

If this host already ran `docker login ghcr.io` (e.g. for the pantry-bot
migration), the same config can be converted directly instead of minting a
new PAT-based Secret:

```bash
kubectl -n opsbot create secret generic ghcr-pull-secret \
  --from-file=.dockerconfigjson=$HOME/.docker/config.json \
  --type=kubernetes.io/dockerconfigjson
```

Check first that the file has a real base64 `auth` entry for `ghcr.io`, not a
`credsStore`/`credHelpers` reference — if Docker delegated to a credential
helper, this file holds nothing usable and the resulting Secret is silently
empty. Use the explicit `create secret docker-registry` form above in that
case.

---

## Not in scope for this Secret

RCON access (`k8s-homelab-bi6.4`) is a separate, not-yet-implemented slash
command. When that lands, it will reuse `minecraft-rcon`'s existing
`rcon.password` value (see `../minecraft/secrets.md`) — either by having
opsbot read that Secret directly (would need a `secrets: get` RBAC grant
scoped to that one named Secret, not blanket) or by duplicating the value
into an opsbot-owned Secret so opsbot's RBAC never needs any `secrets` verb
at all. That decision is deferred to `bi6.4`; `20-rbac.yaml` today grants
zero `secrets` access on purpose.

---

## `opsbot-github` — fine-grained PAT for `/bug` (GitHub Issues)

`/bug` (k8s-homelab-cq8) files bug reports as GitHub issues on the repo that
owns the affected bot — `ChayzX/k8s-homelab` for `music`, `ChayzX/pantry-bot`
for `pantry` (see `bot/util.py` `BOT_REPOS`). It calls the GitHub REST API
over outbound HTTPS from `bot/gh_ops.py`; no local DB, no hostPath mounts.

Create a **fine-grained PAT** (not classic):

1. https://github.com/settings/personal-access-tokens/new → **Generate new token**
2. **Repository access**: Only select repositories → `ChayzX/k8s-homelab` + `ChayzX/pantry-bot`
3. **Permissions**: Repository → **Issues: Read and write**
4. Name it e.g. `opsbot-github`, set an expiry, generate.
5. Store the value in the Secret:

```bash
kubectl -n opsbot create secret generic opsbot-github \
  --from-literal=GITHUB_TOKEN='<FINE_GRAINED_PAT>'
```

The key name must match exactly — the Deployment consumes it via `secretKeyRef`
(`env: GITHUB_TOKEN`), which `bot/gh_ops.py` reads at create time.

Scope of impact: this token can only read + write issues in those two repos —
no repo contents, no other repos. It has **zero** permission to trigger
workflows, push code, or touch `ChayzX/Operations-ios-app` / `ChayzX/aios`
even though those boards also migrated. (If `/bug` later grows to file issues
on other repos, add them to the PAT's repo selection + `BOT_REPOS` together.)

Retired token note: the original `/bug` used a local issue tracker and needed
no GitHub token. That backend is gone — if the Secret
already exists from an earlier flow, delete it and recreate with the fine-
grained PAT above (`kubectl -n opsbot delete secret opsbot-github`).

---

## Checklist before applying `40-deployment.yaml`

```bash
kubectl -n opsbot get secret opsbot-discord opsbot-witness ghcr-pull-secret opsbot-github
```

All four must exist. Never commit them, never `kubectl get -o yaml` them into a
paste.
