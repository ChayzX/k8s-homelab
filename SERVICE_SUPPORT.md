# Service source and support map

This is the quick reference for future maintenance. Kubernetes manifests and
operational scripts are in the `ChayzX/k8s-homelab` repository; application
source is called out separately where it lives elsewhere. Never put runtime
secrets, SQLite databases, world data, or kubeconfigs into Git.

## Service map

| Service | Source of truth | Runtime | Safe access and maintenance |
|---|---|---|---|
| PantryBot | [ChayzX/pantry-bot](https://github.com/ChayzX/pantry-bot), local checkout `/home/chase/Downloads/pantry-bot`; k8s manifests in `pantry-bot/` | `pantry-bot` namespace; Cloudflare Tunnel routes OAuth/overlay traffic | Merging to the PantryBot repo's `main` builds, applies, restarts, and verifies automatically (see `DEPLOYING.md`'s trigger matrix) — a merge is the deploy approval, not a separate manual click. Secrets and the production SQLite PVC are documented in `pantry-bot/SECRETS.md`. Do not merge to `main` casually. |
| JMusicBot | Published multi-architecture GHCR image from `.github/workflows/jmusicbot-deploy.yml`; deployment manifests in `jmusicbot/` | `jmusicbot` namespace; immutable GHCR image, R2-backed `emptyDir` state | Use the GitHub Actions workflow and `docs/recovery/ORACLE-JMUSICBOT-MIGRATION.md`. Preserve `Recreate`, the immutable image reference, and one-writer fencing. Do not resurrect the retired local updater or PVC-based state path. |
| Minecraft | `minecraft/` in this repo; Paper image build and updater in `minecraft/Dockerfile` and `scripts/minecraft-auto-update.sh` | `minecraft` namespace; `minecraft-world` PVC; RCON is ClusterIP-only | Use `minecraft/MIGRATION.md`, `minecraft/secrets.md`, and `minecraft/BEDROCK.md`. Back up before upgrades. Keep Recreate strategy, RCON preStop shutdown, 120-second grace period, and 8 GiB memory limit. Never edit the world PVC directly while the pod is running. |
| Opsbot | `opsbot/bot/` in this repo; image published to GHCR | `opsbot` namespace; Discord outbound gateway only | Merging changes under `opsbot/bot/**` builds, applies, restarts, and verifies automatically — no second click, merge is approval. RBAC is in `opsbot/20-rbac.yaml`; secrets are in `opsbot/SECRETS.md`. Use `/pods`, `/deploy`, `/mc`, and `/bug` through Discord for supported operations. |
| Grafana | Dashboards in `dashboards/`; manifests/provisioning in `observability/` | `observability` namespace, Service port 3002, external hostname `grafana.greeniespantry.uk` | Pushing dashboard/config changes to `main` applies the ConfigMap, restarts, and verifies Grafana automatically via `grafana-deploy.yml` (a manual workflow dispatch also works, but isn't required). Datasource and alert provisioning are in `observability/grafana-provisioning.yaml`; credentials are in `observability/SECRETS.md`. |
| Prometheus | `observability/prometheus*.yaml` | `observability` namespace, internal Service | Inspect targets and rules with `kubectl`; change scrape configuration in Git and apply through the observability workflow/runbook. Persistent data is on the Prometheus PVC. |
| Loki / Promtail | `observability/loki*.yaml` and `observability/promtail*.yaml` | `observability` namespace | Loki stores logs; Promtail labels and parses them. Changes to labels affect dashboards and the host watcher, so update queries/tests together. |
| Monitoring | `observability/k3s-watcher/`, Prometheus, and UptimeRobot | k3s-watcher sends Discord/Operations alerts; UptimeRobot checks public reachability | Keep functional workload URLs in the host-local watcher environment. Use per-node UptimeRobot Ping/Port monitors for node identity; use existing public hostnames for HTTP checks. |
| Cloudflare connectors | `pantry-bot/60-deployment-cloudflared.yaml` and `ci-tunnel/20-deployment.yaml` | `pantry-bot` and `ci-tunnel` namespaces | Check `/ready` on metrics port 2000 and connector logs. Dashboard-managed hostname routing lives in Cloudflare, not this repo. Keep DNS, dial, origin, and readiness failures alertable. |
| k3s-watcher | Tracked source in `observability/k3s-watcher/`; host-installed copy consumed by `~/.config/systemd/user/k3s-watcher.service` | Host systemd user service; polls Loki and Kubernetes, sends Discord DMs | Copy/install from the tracked source, keep `.env` host-local, run `PYTHONPATH=. python3 test_watcher.py`, then restart the user unit. It health-gates only narrow Cloudflared QUIC teardown noise and deduplicates fingerprints. |

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
- `.github/workflows/deploy.yml` in the PantryBot repo — the single
  consolidated "PantryBot CI/CD" workflow (build, apply, restart, verify,
  automatically on every merge to `main`; `publish.yml` no longer exists as
  a separate file). Never commit `.env`, OAuth tokens, or the production
  database.

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
- `opsbot/bot/gh_ops.py`, `opsbot/bot/k8s_ops.py` — GitHub issue filing
  (`/bug`) and allowlisted pod/deploy operations.
- `opsbot/40-deployment.yaml` — image, probes, and environment wiring;
  `opsbot/20-rbac.yaml` is the authorization boundary.
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
- `pantry-bot/60-deployment-cloudflared.yaml` and
  `ci-tunnel/20-deployment.yaml` — Cloudflare connector Deployments; hostname
  ingress rules are managed in Cloudflare, not source control.
- `observability/k3s-watcher/watcher.py` — Loki polling, restart-loop checks,
  readiness-gated Cloudflared suppression, persistence confirmation, and
  Discord alerting. Its host `.env` and systemd unit are intentionally local.
- `.github/workflows/grafana-deploy.yml` — dashboard ConfigMap/apply/restart/
  rollout/health pipeline.

### Scotty and Beads (retired)

Beads is retired: issue tracking lives in GitHub Issues + Projects v2 (see
`AGENTS.md`). The `scotty` namespace, `/home/chase/bead-me-up-scotty/`, the
host-side `bd` CLI, and all local `.beads/` databases are retired and must
not be resurrected. Historical references to them in this file are
provenance only.

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

Project work is tracked only in GitHub Issues and the GitHub Projects board;
use `gh issue view`, `gh issue comment`, and the repository's current issue
workflow. Do not recreate the retired Beads database, Scotty service, or local
issue-tracking commands.

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
