# Deploying Code Changes

Use this rule: a green **publish/CI** run does not necessarily mean the live
pod changed. Only a workflow whose summary contains **Apply**, **Restart**, and
**Verify** stages has deployed a workload.

## At-a-glance trigger matrix

| Change | Push to `main` does | Manual action needed? |
|---|---|---|
| Opsbot application (`opsbot/bot/**`) | Builds/publishes GHCR image only | Yes: run **Opsbot CI/CD** |
| PantryBot application | Builds/publishes image only | Yes: run PantryBot **Deploy** |
| Grafana dashboards/config | Applies ConfigMap, restarts, and verifies Grafana automatically | No; push is the deployment (a manual dispatch also works) |
| Minecraft updater/manifests | No GitHub deployment | Yes: follow the Minecraft runbook/approved updater path |
| JMusicBot host build | No GitHub deployment | Yes: run the host updater manually/cron |
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

Deployment is manual — you click the button in GitHub's UI. This ensures you're aware of what's going live.

### For opsbot

1. Go to GitHub → k8s-homelab repo → **Actions** tab
2. Click **Deploy opsbot** workflow
3. Click **Run workflow** (top right)
4. Leave "Use workflow from" as `main`
5. Optionally type an image tag in the text field (e.g., `abc1234` for a specific commit, or `latest` for the most recent build). Leave blank to default to `latest`.
6. Click the green **Run workflow** button
7. In the run, confirm all four jobs complete: **Build/publish**, **Apply
   manifest**, **Deploy/restart**, and **Verify rollout and pod health**. The
   final job summary names the old and new pod and reports readiness/image ID.

### For pantry-bot

Same steps, but for the **pantry-bot** repo. A push to `main` publishes the
image; it does not change the running pod:

1. GitHub → pantry-bot repo → **Actions** tab
2. Click **Deploy** workflow
3. Click **Run workflow**, leave settings as-is (or optionally specify an image tag)
4. Click the green **Run workflow** button and wait for the staged publish,
   apply, restart, and verify results.

### For Grafana

Push dashboard/config changes to `main`. The `Deploy Grafana` workflow runs
the ConfigMap apply, pod restart, rollout wait, and health checks automatically.
You do not need a second Run workflow click. Use manual dispatch only when you
want to redeploy the current dashboard state without a new commit.

## If Something Goes Wrong

- **Pod doesn't become healthy**: the deploy workflow automatically rolls back to the previous version after 60 seconds. Check the Actions log for error details.
- **Deployment stays unhealthy**: the Discord monitoring alert system (`k3s-watcher`) will DM you on Discord within a few minutes if pods are crashing or erroring. Check the bot's logs: `kubectl logs -n opsbot deployment/opsbot` (or `kubectl logs -n pantry-bot deployment/pantry-bot`).
- **Workflow itself fails during publish**: fix the code, commit, push to `main` again to rebuild. Nothing was deployed.
- **Workflow fails during Apply/Restart/Verify**: inspect the failing stage and
  the final summary. The old pod may still be serving; do not click Run again
  repeatedly until the failure cause is understood.

## Previous Auto-Deploy System

Keel (the old auto-deploy service) was retired after a credential failure went unnoticed for a full day. The manual-click model ensures you see the result immediately in GitHub's UI and know when something goes live.
