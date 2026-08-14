# Service source and support map

This is the quick reference for future maintenance. Kubernetes manifests and
operational scripts are in the `ChayzX/k8s-homelab` repository; application
source is called out separately where it lives elsewhere. Never put runtime
secrets, SQLite databases, world data, or kubeconfigs into Git.

## Service map

| Service | Source of truth | Runtime | Safe access and maintenance |
|---|---|---|---|
| PantryBot | [ChayzX/pantry-bot](https://github.com/ChayzX/pantry-bot), local checkout `/home/chase/Downloads/pantry-bot`; k8s manifests in `pantry-bot/` | `pantry-bot` namespace; Cloudflare Tunnel routes OAuth/overlay traffic | Build runs from the PantryBot repo; deploy only through its manual GitHub Actions workflow. Secrets and the production SQLite PVC are documented in `pantry-bot/SECRETS.md`. Do not restart or apply this service casually. |
| JMusicBot | Host checkout `/home/chase/docker/jmusicbot` (no Git remote currently); deployment manifests in `jmusicbot/` | `jmusicbot` namespace; local image `jmusicbot-custom:<tag>` side-loaded into k3s | Use `scripts/auto-update.sh` or the runbook in `jmusicbot/README.md`. Preserve `imagePullPolicy: IfNotPresent`; k3s cannot pull the local-only image. State is on the `jmusicbot-config` PVC. |
| Minecraft | `minecraft/` in this repo; Paper image build and updater in `minecraft/Dockerfile` and `scripts/minecraft-auto-update.sh` | `minecraft` namespace; `minecraft-world` PVC; RCON is ClusterIP-only | Use `minecraft/MIGRATION.md`, `minecraft/secrets.md`, and `minecraft/BEDROCK.md`. Back up before upgrades. Keep Recreate strategy, RCON preStop shutdown, 120-second grace period, and 8 GiB memory limit. Never edit the world PVC directly while the pod is running. |
| Opsbot | `opsbot/bot/` in this repo; image published to GHCR | `opsbot` namespace; Discord outbound gateway only | Push source to trigger publish, then manually run the deploy workflow. RBAC is in `opsbot/20-rbac.yaml`; secrets are in `opsbot/SECRETS.md`. Use `/pods`, `/deploy`, `/mc`, and `/bug` through Discord for supported operations. |
| Grafana | Dashboards in `dashboards/`; manifests/provisioning in `observability/` | `observability` namespace, Service port 3002, external hostname `grafana.greeniespantry.uk` | Dashboard changes use `grafana-deploy.yml` and require a manual workflow dispatch. Datasource and alert provisioning are in `observability/grafana-provisioning.yaml`; credentials are in `observability/SECRETS.md`. |
| Prometheus | `observability/prometheus*.yaml` | `observability` namespace, internal Service | Inspect targets and rules with `kubectl`; change scrape configuration in Git and apply through the observability workflow/runbook. Persistent data is on the Prometheus PVC. |
| Loki / Promtail | `observability/loki*.yaml` and `observability/promtail*.yaml` | `observability` namespace | Loki stores logs; Promtail labels and parses them. Changes to labels affect dashboards and the host watcher, so update queries/tests together. |
| Uptime Kuma | `observability/uptime-kuma.yaml` | `observability` namespace, Service port 3001, external hostname `status.greeniespantry.uk` | Manage monitors in the Kuma UI. Host heartbeat setup is documented in `scripts/README.md`; do not put push tokens in Git. |
| Cloudflare connectors | `pantry-bot/60-deployment-cloudflared.yaml` and `ci-tunnel/20-deployment.yaml` | `pantry-bot` and `ci-tunnel` namespaces | Check `/ready` on metrics port 2000 and connector logs. Dashboard-managed hostname routing lives in Cloudflare, not this repo. Keep DNS, dial, origin, and readiness failures alertable. |
| k3s-watcher | Tracked source in `observability/k3s-watcher/`; host-installed copy consumed by `~/.config/systemd/user/k3s-watcher.service` | Host systemd user service; polls Loki and Kubernetes, sends Discord DMs | Copy/install from the tracked source, keep `.env` host-local, run `PYTHONPATH=. python3 test_watcher.py`, then restart the user unit. It health-gates only narrow Cloudflared QUIC teardown noise and deduplicates fingerprints. |
| Scotty / bead-me-up-scotty | Upstream checkout `/home/chase/bead-me-up-scotty` ([upstream](https://github.com/brendan-appstart/bead-me-up-scotty)); manifests in `scotty/` | `scotty` namespace, hostPath-mounted config and Beads databases | Build and side-load `bead-me-up-scotty:local` as described in `scotty/README.md`. Config is `/home/chase/.config/bead-me-up-scotty-k8s/config.json`; restart after hand edits because config is cached. |

## File-level map

### PantryBot (`/home/chase/Downloads/pantry-bot`)

- `src/bot/index.ts` — application entrypoint, Twitch chat wiring, EventSub
  redemption handling, overlay broadcasts, and command registration.
- `src/bot/commands/*.ts` — one module per chat command (`grab`, `donate`,
  `pantry`, `boss`, `restock`, `pantryreward`, `overlayVolume`, and control
  commands). Authorization and cooldown behavior is in
  `src/bot/commandRegistry.ts` and `src/bot/cooldowns.ts`.
- `src/game/catalog.ts` — snack IDs, display names, legacy names, rarity
  pools, and lookup behavior. Update this file when a snack is renamed.
- `src/game/boss.ts`, `src/game/inventory*`, `src/game/restock.ts` — boss
  damage/donations, inventory persistence calls, and restock events.
- `src/db/schema.ts`, `src/db/index.ts`, `src/db/stateRepo.ts`, and `src/db/*Repo.ts`
  — SQLite schema, opening/migrations, app state, auth, inventory, and boss
  persistence.
- `src/twitch/channelPoints.ts`, `src/twitch/eventsub/*`, and
  `src/twitch/tokenStore.ts` — reward lifecycle, EventSub subscriptions,
  redemption awards, and OAuth token refresh.
- `src/overlay/server.ts`, `src/overlay/volume.ts`, and
  `src/overlay/public/` — WebSocket replay/broadcast server, persisted sound
  volume, theme, ticker, and OBS browser-source pages.
- `test/*.test.ts` — Vitest coverage; run `npm test` and `npm run typecheck`.
- `.github/workflows/publish.yml` and `deploy.yml` in the PantryBot repo —
  image publish and manual deployment. Never commit `.env`, OAuth tokens, or
  the production database.

### JMusicBot (`/home/chase/docker/jmusicbot` plus this repo)

- `jmusicbot/40-deployment-jmusicbot.yaml` — Java bot Deployment, image,
  probes, mounts, and resource limits.
- `jmusicbot/50-deployment-release-notifier.yaml` — release notifier.
- `jmusicbot/20-configmap.yaml` and `jmusicbot/30-pvcs.yaml` — non-secret
  configuration and persistent state declarations.
- `scripts/auto-update.sh` — host build/import/rollout/rollback updater.
- `scripts/minecraft_exporter.py`, `scripts/README.md` — host metrics and
  operational runbook. The Java application source remains in the host
  checkout; this repository owns the k3s wrapper and deployment contract.

### Minecraft

- `minecraft/minecraft.yaml` — namespace, PVC, Services, Deployment,
  readiness/liveness probes, RCON preStop, and resource policy.
- `minecraft/Dockerfile` — Paper/Java image build and plugin installation.
- `minecraft/entrypoint.sh` — empty-data guard, server startup, and JVM
  arguments.
- `minecraft/Rcon.java` — small RCON client used by probes and shutdown.
- `minecraft/geyser-config.yml`, `minecraft/BEDROCK.md`, and `scripts/minecraft-download-plugins.sh`
  — Bedrock/Geyser/Floodgate configuration and verified plugin downloads.
- `scripts/minecraft-auto-update.sh` — Paper build discovery, checksum,
  image build/import, rollout, RCON verification, rollback, and state file.
- `scripts/minecraft_backup.py` — PVC/world archive job; backups are outside
  Git and must be checked before upgrades.
- `minecraft/MIGRATION.md`, `minecraft/UPGRADE-26.2.md`, and
  `minecraft/secrets.md` — cutover, upgrade, and secret procedures.

### Opsbot

- `opsbot/bot/main.py` — Discord bot startup and command registration.
- `opsbot/bot/commands.py`, `opsbot/bot/bd_ops.py`, and
  `opsbot/bot/minecraft_ops.py` — allowlisted pod/deploy operations, Beads bug
  filing, and Minecraft/RCON operations.
- `opsbot/40-deployment.yaml` — image, hostPath Beads mounts, probes, and
  environment wiring; `opsbot/20-rbac.yaml` is the authorization boundary.
- `.github/workflows/opsbot-deploy.yml` — publish/apply/restart/verify stages;
  `opsbot/SECRETS.md` documents required secrets.

### Observability and Cloudflare

- `observability/prometheus-config.yaml` — scrape jobs and targets.
- `observability/loki-config.yaml` and `observability/promtail-config.yaml`
  — log storage, parsing, labels, and retention.
- `observability/grafana.yaml` and `observability/grafana-provisioning.yaml`
  — Grafana Deployment, datasources, and alert provisioning.
- `dashboards/*.json` and `dashboards/dashboards-configmap.yaml` — dashboard
  panels and their ConfigMap packaging.
- `observability/uptime-kuma.yaml` — Kuma Deployment/PVC/Service.
- `pantry-bot/60-deployment-cloudflared.yaml` and
  `ci-tunnel/20-deployment.yaml` — Cloudflare connector Deployments; hostname
  ingress rules are managed in Cloudflare, not source control.
- `observability/k3s-watcher/watcher.py` — Loki polling, restart-loop checks,
  readiness-gated Cloudflared suppression, persistence confirmation, and
  Discord alerting. Its host `.env` and systemd unit are intentionally local.
- `.github/workflows/grafana-deploy.yml` — dashboard ConfigMap/apply/restart/
  rollout/health pipeline.

### Scotty and Beads

- `scotty/10-deployment.yaml` — hostPath mounts for config and the two local
  Beads databases; `scotty/20-service.yaml` exposes the UI internally.
- `/home/chase/bead-me-up-scotty/app/` — upstream UI routes/pages.
- `/home/chase/bead-me-up-scotty/lib/bd.ts` — Beads command execution and
  actor stamping; `lib/config.ts` — persisted config; `lib/attribution.ts` —
  human/agent origin classification.
- `.beads/dolt/` — live local issue database; sync with `bd dolt pull/push`.
  `.beads/issues.jsonl` is an export, not the sync source of truth.

## Common access patterns

Read-only cluster triage:

```bash
kubectl get pods -A
kubectl get events -A --sort-by=.lastTimestamp
kubectl logs -n <namespace> deploy/<deployment> --tail=200
kubectl rollout status -n <namespace> deploy/<deployment>
```

For Git-managed changes, edit the relevant directory, run the repository
quality checks, and use the matching workflow's manual dispatch. The CI
tunnel and scoped kubeconfigs are documented in `ci-tunnel/MANUAL-SETUP.md`
and `ci-deploy/README.md`.

For Beads, use `bd show`, `bd ready`, and `bd update <id> --claim --actor CodeX`;
sync with `bd dolt push`. Scotty reads its mounted local databases directly;
it does not automatically pull another computer's Beads changes. Run
`bd dolt pull` on the host that owns the mounted database when cross-machine
updates are expected.

## Recovery rules

- Prefer a manifest/image rollback over editing live PVC contents.
- Verify the replacement pod is Ready and check its startup logs after every
  restart.
- Keep PantryBot deploys isolated from observability, Minecraft, and Opsbot
  changes; a non-Pantry change must not alter the PantryBot PVC, Deployment,
  Service, or tunnel route.
- Treat Cloudflare connector teardown messages as noise only when `/ready` is
  healthy. DNS, dial, origin, and readiness failures are actionable.
- Keep secrets in Kubernetes Secrets, GitHub Actions secrets, or host-local
  `.env` files. Never commit them or paste decoded values into support notes.
