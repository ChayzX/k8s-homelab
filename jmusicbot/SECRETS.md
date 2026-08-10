# Secrets — `jmusicbot` namespace

No secret values live in this repo. Every Secret below is created imperatively
by the operator, on the node, before the Deployments are applied.

Run these **after** `_bootstrap/00-namespaces.yaml` and **before**
`40-deployment-jmusicbot.yaml` / `50-deployment-release-notifier.yaml`.
A Deployment whose Secret does not exist yet will sit in
`CreateContainerConfigError` until it does — that is recoverable, not fatal.

---

## 1. `jmusicbot-config-txt` — the whole JMusicBot config file

JMusicBot has no environment-variable configuration path. Everything, including
the Discord bot token, lives in a single flat `config.txt` (HOCON). Splitting
the token out would mean templating the file at runtime, which needs an
initContainer and a second source of truth for every other setting.

The clean answer for a flat config file is: **the whole file is the secret.**

```bash
kubectl -n jmusicbot create secret generic jmusicbot-config-txt \
  --from-file=config.txt=/home/chase/docker/jmusicbot/config.txt
```

The Secret key must be exactly `config.txt` — the Deployment mounts it with
`subPath: config.txt` onto `/musicbot/config.txt`.

Verify the key name and size without printing the token:

```bash
kubectl -n jmusicbot get secret jmusicbot-config-txt \
  -o jsonpath='{range .data.*}{@}{"\n"}{end}' | wc -c   # non-zero
kubectl -n jmusicbot get secret jmusicbot-config-txt -o jsonpath='{.data}' | grep -o 'config.txt'
```

### Updating it later

```bash
kubectl -n jmusicbot create secret generic jmusicbot-config-txt \
  --from-file=config.txt=/path/to/new/config.txt \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n jmusicbot rollout restart deployment/jmusicbot
```

The `rollout restart` is **required**, not optional: `subPath` volume mounts are
snapshotted at pod start and never receive Secret updates.

### Honest limitation

This is config-delivery hygiene, not true secrets hygiene. The token is:

- in `/home/chase/docker/jmusicbot/config.txt` on disk (source of truth),
- base64-encoded (not encrypted) in etcd, unless you enable encryption-at-rest,
- readable as plaintext inside the pod at `/musicbot/config.txt`,
- readable by anyone with `get secrets` in this namespace.

It is materially better than a bind mount only in that it is no longer coupled
to a host path and is not in git. Do not describe it as more than that.

---

## 2. `jmusicbot-notifier-secrets` — Discord webhook for the release notifier

Currently supplied by `/home/chase/docker/jmusicbot/.env`, whose only key is
`DISCORD_RELEASE_WEBHOOK_URL`.

```bash
kubectl -n jmusicbot create secret generic jmusicbot-notifier-secrets \
  --from-literal=DISCORD_RELEASE_WEBHOOK_URL='https://discord.com/api/webhooks/REPLACE/ME'
```

Or, to avoid the value ever touching your shell history, source it from the
existing `.env` (this file contains exactly that one key):

```bash
kubectl -n jmusicbot create secret generic jmusicbot-notifier-secrets \
  --from-env-file=/home/chase/docker/jmusicbot/.env
```

A Discord webhook URL is a bearer credential — anyone holding it can post to
that channel as the webhook. Treat it like a token.

---

## 3. Image pull secret — **not required in this namespace**

`ghcr.io/chayzx/jmusicbot-release-notifier` is a public GHCR package (verified:
anonymous manifest fetch returns HTTP 200). `jmusicbot-custom:yts1182` is
locally built and side-loaded, never pulled.

If the notifier package is ever made private, GHCR starts returning 403 on
anonymous pulls and the pod goes `ImagePullBackOff`. Fix:

```bash
kubectl -n jmusicbot create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=chayzx \
  --docker-password='<GITHUB_PAT_WITH_read:packages>'
```

then add to the notifier pod spec:

```yaml
      imagePullSecrets:
        - name: ghcr-pull-secret
```

Secrets are namespaced — the `ghcr-pull-secret` created in `pantry-bot` does
**not** apply here and must be created separately.

---

## Checklist before applying the Deployments

```bash
kubectl -n jmusicbot get secret jmusicbot-config-txt jmusicbot-notifier-secrets
```

Both must exist. Neither should ever be committed, exported to a file in this
repo, or included in a `kubectl get -o yaml` paste.
