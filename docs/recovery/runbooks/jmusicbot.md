# JMusicBot runbook

JMusicBot runs only on minecraftmachine (home k3s, amd64) in the `jmusicbot`
namespace. There is no standby site and no witness lease: the Oracle standby
was retired on 2026-10-11. This runbook does not cover Minecraft.

- The Deployment uses `Recreate` and is pinned to minecraftmachine, so at most
  one pod connects to Discord and writes the R2 prefix.
- State lives in an `emptyDir`. The `restore-state` init container copies it
  from `r2:pantry-bot-backups/jmusicbot/` on start, and the `r2-sync` sidecar
  syncs it back every 5 minutes.
- `.jmusicbot-r2-heartbeat` is rewritten after each completed sync. It is a
  freshness signal only and is never restored into bot state.

## Health check

```bash
kubectl -n jmusicbot get deploy,pod,svc -o wide
kubectl -n jmusicbot get endpoints jmusicbot-health -o yaml
kubectl -n jmusicbot logs deploy/jmusicbot -c jmusicbot --since=15m \
  | grep -E 'Discord|WebSocket|ERROR' | tail -50
```

`/health` returns 200 only after the bot has logged into Discord; `/live`
returns 200 while the JVM is alive.

### R2 freshness

Do not print credentials, signed URLs, or file contents:

```bash
kubectl -n jmusicbot logs deploy/jmusicbot -c r2-sync --since=30m | tail -50
```

A heartbeat older than ~10 minutes means the sync loop is failing.

## Restore after losing the node

1. Rebuild k3s on minecraftmachine and recreate the Secrets in
   `jmusicbot/SECRETS.md` (`jmusicbot-config-txt`, `jmusicbot-notifier-secrets`,
   `jmusicbot-r2`).
2. `kubectl apply -k jmusicbot/`, then set the current image with the
   GitHub Actions workflow (or `kubectl set image` to the last known-good tag).
3. The init container restores state from R2; confirm `/health` turns ready.

## Validation and rollback

```bash
bash tests/jmusicbot-secret-restore-rbac-test.sh
```

Roll back a bad release with `kubectl -n jmusicbot rollout undo
deployment/jmusicbot` (the deploy workflow does this automatically when
verification fails). Never delete R2 generations while recovering.
