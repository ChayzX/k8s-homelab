# Secrets — `opsbot` namespace

No secret values live in this repo. Create the Secret below imperatively,
**before** applying `40-deployment.yaml`. This mirrors every other bot in
this repo — see `../pantry-bot/SECRETS.md`, `../keel/SECRETS.md`,
`../minecraft/secrets.md`.

---

## `opsbot-discord` — bot token + authorization allowlist

Two values, one Secret:

| Key | Meaning |
|---|---|
| `DISCORD_BOT_TOKEN` | The bot token from the Discord Developer Portal application (see `bd show k8s-homelab-bi6.1` — set up as a separate task, not yet done as of this writing). |
| `DISCORD_USER_ID` | The single Discord user ID allowed to invoke commands. Reused from the existing convention already used by `scripts/minecraft_backup.py` and `scripts/minecraft_exporter.py` (`929216447723499562` — confirmed via `bd show k8s-homelab-bi6.5`'s notes, not a new value). |

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
yet** (`bd show k8s-homelab-bi6.1`) — `40-deployment.yaml` references it by
name and will sit in `CreateContainerConfigError` until it exists, which is
the intended, loud failure mode (same pattern as `../minecraft/minecraft.yaml`
before its Secret is created).

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

## Checklist before applying `40-deployment.yaml`

```bash
kubectl -n opsbot get secret opsbot-discord
```

Must exist. Never commit it, never `kubectl get -o yaml` it into a paste.
