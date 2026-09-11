# Secret Restoration Matrix

This is a names-only inventory. Secret values must remain in Kubernetes or an
approved external secret store; this file must never contain decoded data.
The matrix was reconciled against the live cluster on 2026-09-10.

| Area | Live Secret names | Recovery use | Status |
|---|---|---|---|
| Authentik/Postgres | `auth/auth-authentik`, `auth/auth-postgresql` | Authentik configuration, database connection, and database restore | Backup and isolated readiness restore verified; interactive login still open |
| LDAP/outpost | `auth/ldap-bind-service-account`, `auth/ldap-chase-initial-password`, `auth/ldap-outpost-token` | LDAP bind, emergency identity bootstrap, and outpost connectivity | Values not yet reconstructed in an isolated promotion |
| Minecraft | `minecraft/minecraft-rcon` | RCON save/health and controlled promotion | Live; offsite recovery archive intentionally excludes the password |
| JMusicBot | `jmusicbot/jmusicbot-config-txt`, `jmusicbot/jmusicbot-notifier-secrets`, `jmusicbot/jmusicbot-r2` | Bot credentials, notifier state, and R2 restore | R2 file restore verified; complete startup/promotion still open |
| PantryBot | `pantry-bot/cloudflared-tunnel`, `pantry-bot/commands-cloudflared-tunnel-token`, `pantry-bot/ghcr-pull-secret`, `pantry-bot/pantry-bot-litestream`, `pantry-bot/pantry-bot-twitch`, `pantry-bot/pantry-bot-platform`, `pantry-bot/pantry-bot-witness` | Shared private tunnel, dedicated public commands tunnel, image pull, Litestream restore, Twitch writer identity, PostgreSQL/site runtime identity, and neutral witness authority | Commands-only token is not provisioned until the dedicated Cloudflare tunnel exists; platform/witness Secret names and required keys are now present in home and Oracle; writer fencing and promotion remain open |
| Opsbot | `opsbot/ghcr-pull-secret`, `opsbot/opsbot-discord`, `opsbot/opsbot-github` | Image pull, Discord control, and GitHub operations | No independent cloud promotion tested |
| Operations | `operations/ghcr-pull`, `operations/operations-alert-ingest`, `operations/operations-authentik-audit`, `operations/operations-minecraft-rcon`, `operations/operations-terminal`, `operations/operations-web-push` | Dashboard deployment, alert ingestion, terminal access, RCON, and push notifications | SQLite restore/readiness verified; emergency access without Authentik still open |
| Observability | `observability/grafana-cloud-loki` | External log delivery | External log path is present; dashboard restore not rehearsed |
| CI/tunnel | `ci-tunnel/ci-tunnel-token`, plus per-namespace `ci-deploy-token` copies | CI tunnel and scoped deployment access | Reprovisioning procedure exists; independent cloud deployment path not tested |

Helm release and node-password Secrets are also present in `kube-system`; they
are cluster-generated implementation data and are not substitutes for the
root-only k3s server token described in
[`K3S-DATASTORE-BACKUP.md`](K3S-DATASTORE-BACKUP.md).

## Verification commands

These commands print names and key names only:

```sh
kubectl get secrets -A --no-headers | awk '{print $1 "/" $2}' | sort
kubectl get secret -n <namespace> <name> -o json | jq -r '.data | keys[]'
```

Issue #192 remains the tracking location for external secret-store
reconstruction. A recovery gate is not complete merely because a Secret object
exists in the home cluster; the value must be available independently and be
validated in the isolated restore procedure for the service that uses it.
