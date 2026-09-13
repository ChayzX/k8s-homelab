#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dir="$root/pantry-bot"
runbook="$root/docs/recovery/runbooks/pantrybot.md"

# The old monolith and shared tunnel must not be part of a broad YAML apply.
test -f "$dir/40-deployment.yaml.retired"
test -f "$dir/50-service.yaml.retired"
test -f "$dir/60-deployment-cloudflared.yaml.retired"
test ! -e "$dir/40-deployment.yaml"
test ! -e "$dir/50-service.yaml"
test ! -e "$dir/60-deployment-cloudflared.yaml"

for role in \
  62-deployment-commands-cloudflared.yaml \
  66-deployment-app-cloudflared.yaml; do
  test -f "$dir/$role"
done

for name in \
  pantry-chat-worker \
  pantry-commands-site \
  pantry-overlay-delivery \
  pantry-private-api \
  pantry-private-site \
  pantry-twitch-dispatcher \
  pantry-twitch-gateway; do
  grep -Fqs "$name" "$runbook" || {
    echo "missing split PantryBot role: $name" >&2
    exit 1
  }
done

echo 'pantrybot-split-architecture-test=passed'
