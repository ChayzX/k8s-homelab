#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The runtime EnvironmentFile is host-local. A tracked watcher.env would make
# accidental credential commits easy, so only the non-secret example is allowed.
if [[ -e "$root/observability/k3s-watcher/watcher.env" ]]; then
  echo "watcher.env must not be tracked" >&2
  exit 1
fi
if git -C "$root" ls-files -z | xargs -0 rg -n -I \
  -e 'DISCORD_BOT_TOKEN=MT[A-Za-z0-9._-]{20,}' \
  >/dev/null 2>&1; then
    echo "tracked Discord watcher credential detected" >&2
    exit 1
fi

test -f "$root/observability/k3s-watcher/watcher.env.example"
grep -q '^DISCORD_BOT_TOKEN=$' "$root/observability/k3s-watcher/watcher.env.example"
grep -q '^DISCORD_USER_ID=$' "$root/observability/k3s-watcher/watcher.env.example"
echo "watcher-secret-hygiene-test: passed"
