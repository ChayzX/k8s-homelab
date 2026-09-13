#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

for manifest in 40-deployment.yaml 50-service.yaml 60-deployment-cloudflared.yaml; do
  test ! -e "$root/pantry-bot/$manifest"
  test -e "$root/pantry-bot/$manifest.retired"
done

grep -q 'public commands UI' "$root/docs/recovery/PANTRYBOT-ACTIVE-ACTIVE-RUNBOOK.md"
grep -q 'Twitch gateway replicas' "$root/docs/recovery/PANTRYBOT-ACTIVE-ACTIVE-RUNBOOK.md"

echo 'pantrybot-legacy-manifest-guard-test=passed'
