# Deploying Code Changes

Code builds automatically when pushed to `main` — deployment is manual. This two-step model ensures builds work before anything touches the live bots.

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

That's it. GitHub Actions will build and push a Docker image. You'll see the workflow run in the Actions tab. Wait for it to pass (green checkmark) before deploying. (If your k8s-homelab push didn't touch `opsbot/bot/**`, no build runs — that's expected, e.g. a docs or manifest change elsewhere in the repo.)

## Deploying

Deployment is manual — you click the button in GitHub's UI. This ensures you're aware of what's going live.

### For opsbot

1. Go to GitHub → k8s-homelab repo → **Actions** tab
2. Click **Deploy opsbot** workflow
3. Click **Run workflow** (top right)
4. Leave "Use workflow from" as `main`
5. Optionally type an image tag in the text field (e.g., `abc1234` for a specific commit, or `latest` for the most recent build). Leave blank to default to `latest`.
6. Click the green **Run workflow** button
7. Watch the run logs. Deployment waits for the new pod to become healthy; if it doesn't within 60 seconds, the old version automatically rolls back.

### For pantry-bot

Same steps, but for the **pantry-bot** repo:

1. GitHub → pantry-bot repo → **Actions** tab
2. Click **Deploy** workflow
3. Click **Run workflow**, leave settings as-is (or optionally specify an image tag)
4. Click the green **Run workflow** button

## If Something Goes Wrong

- **Pod doesn't become healthy**: the deploy workflow automatically rolls back to the previous version after 60 seconds. Check the Actions log for error details.
- **Deployment stays unhealthy**: the Discord monitoring alert system (`k3s-watcher`) will DM you on Discord within a few minutes if pods are crashing or erroring. Check the bot's logs: `kubectl logs -n opsbot deployment/opsbot` (or `kubectl logs -n pantry-bot deployment/pantry-bot`).
- **Workflow itself fails** (build error): fix the code, commit, push to `main` again to rebuild. No rollback needed — nothing was deployed.

## Previous Auto-Deploy System

Keel (the old auto-deploy service) was retired after a credential failure went unnoticed for a full day. The manual-click model ensures you see the result immediately in GitHub's UI and know when something goes live.
