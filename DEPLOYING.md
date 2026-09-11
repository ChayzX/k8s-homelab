# Deploying Code Changes

Use this rule: a green **publish/CI** run does not necessarily mean the live
pod changed. Only a workflow whose summary contains **Apply**, **Restart**, and
**Verify** stages has deployed a workload.

## At-a-glance trigger matrix

| Change | Push to `main` does | Manual action needed? |
|---|---|---|
| Opsbot application (`opsbot/bot/**`) | Builds, applies, restarts, and verifies on merge | No second click; merge is approval |
| PantryBot application | Builds, applies, restarts, and verifies on merge | No second click; merge is approval |
| Grafana dashboards/config | Applies ConfigMap, restarts, and verifies Grafana automatically | No; push is the deployment (a manual dispatch also works) |
| Minecraft source/image inputs | Resolves newest Paper version, builds, applies, restarts, and verifies on merge | No second click; merge is approval |
| JMusicBot source/patch changes | Resolves latest upstream release, builds, applies, restarts, and verifies on merge | No second click; merge is approval |
| k3s-watcher source | Git changes only | Yes: update the host checkout and restart its systemd user unit |

If the Actions run shows only **Build and publish**, nothing was deployed yet.
If it shows **Apply**, **Restart**, and **Verify**, the live deployment path ran.

## Making a Code Change

Edit code, commit, push to `main`. The build happens automatically:

```bash
# k8s-homelab (opsbot) -- only triggers if the push touches opsbot/bot/**
git push origin main
```

```bash
# pantry-bot -- any push to main triggers a build
git push origin main
```

GitHub Actions will build and push a Docker image. Wait for that run to pass
before manually deploying. (If your k8s-homelab push didn't touch
`opsbot/bot/**`, no Opsbot publish run occurs — that is expected.)

## Deploying

Deployment is merge-gated — merging a PR to `main` is the approval action. The
resulting Actions run contains the numbered publish, apply, restart, and verify
jobs. Manual dispatch remains available only to retry the pipeline; it is not a
separate production approval path.

### For opsbot

1. Open the PR and merge it to `main`.
2. Go to GitHub → k8s-homelab repo → **Actions** tab.
3. Open **Opsbot CI/CD** and confirm all four jobs complete: **Build/publish**, **Apply
   manifest**, **Deploy/restart**, and **Verify rollout and pod health**. The
   final job summary names the old and new pod and reports readiness/image ID.

### For pantry-bot

Merge the PR in the **pantry-bot** repo. The merge run deploys the exact merge
commit:

1. GitHub → pantry-bot repo → **Actions** tab
2. Open **PantryBot CI/CD** and wait for the staged publish,
   apply, restart, and verify results.

### For Grafana

Push dashboard/config changes through a merged PR to `main`. The `Deploy Grafana` workflow runs
the ConfigMap apply, pod restart, rollout wait, and health checks automatically.
You do not need a second Run workflow click.

### For Minecraft

Merging a Minecraft PR runs **Minecraft CI/CD**. It resolves the newest stable
Paper version automatically, verifies Paper/Geyser/Floodgate artifacts, then
publishes, applies, restarts, and verifies the server. The deployment uses the
scoped `KUBE_CONFIG_MINECRAFT` secret and preserves the Recreate strategy/world
PVC. A manual dispatch is only a retry/diagnostic tool.
Before the first deployment, make the `paper-minecraft` GHCR package readable
by the cluster (public package, or an image-pull Secret wired into the
Deployment).

### For JMusicBot

Merging a JMusicBot workflow/patch change runs **JMusicBot CI/CD**. It resolves
the maintainer's latest `arif-banai/MusicBot` release, applies the tracked
voice-channel and health patches, then publishes, applies, restarts, and checks
`/health`. Configure `KUBE_CONFIG_JMUSICBOT` before merging the first deployable
change.
Make the `jmusicbot` GHCR package readable by the cluster before its first
deployment (public package, or an image-pull Secret in the namespace).

## If Something Goes Wrong

- **Pod doesn't become healthy**: the workflow's verify stage fails and the rollback-on-failure job attempts `kubectl rollout undo` to the previous ReplicaSet, then verifies the rollback. Inspect the Actions summary and deployment history even after an automatic rollback.
- **Deployment stays unhealthy**: the Discord monitoring alert system (`k3s-watcher`) will DM you on Discord within a few minutes if pods are crashing or erroring. Check the bot's logs: `kubectl logs -n opsbot deployment/opsbot` (or `kubectl logs -n pantry-bot deployment/pantry-bot`).
- **Workflow itself fails during publish**: fix the code, commit, push to `main` again to rebuild. Nothing was deployed.
- **Workflow fails during Apply/Restart/Verify**: inspect the failing stage and
  the final summary. The old pod may still be serving; do not click Run again
  repeatedly until the failure cause is understood.

## Previous Auto-Deploy System

Keel (the old auto-deploy service) was retired after a credential failure went unnoticed for a full day. Its weakness wasn't automation itself — it was that the automation ran silently, so nobody saw when it broke. The current merge-triggered pipeline is also automatic, but the trigger is visible: opening the PR and merging it, not a background poller, so you see the run and its result in GitHub's UI in real time and know when something goes live.
