#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Every deployable non-Minecraft production workflow must serialize changes to
# its target. This prevents two site/rollback operations from racing and keeps
# the workflow budget from accumulating redundant queued executions.
for workflow in \
  "$root/.github/workflows/grafana-deploy.yml" \
  "$root/.github/workflows/jmusicbot-deploy.yml" \
  "$root/.github/workflows/opsbot-deploy.yml" \
  "$root/.github/workflows/windows-alloy-metrics-deploy.yml"; do
  test -f "$workflow"
  grep -q '^concurrency:$' "$workflow" || {
    echo "missing workflow concurrency contract: $workflow" >&2
    exit 1
  }
  grep -Eq '^  group: [a-z0-9-]+$' "$workflow" || {
    echo "missing static target concurrency group: $workflow" >&2
    exit 1
  }
  grep -q '^  cancel-in-progress: false$' "$workflow" || {
    echo "deploy workflows must not cancel an in-flight rollout: $workflow" >&2
    exit 1
  }
done

if grep -q '^concurrency:$' "$root/.github/workflows/minecraft-deploy.yml"; then
  echo 'Minecraft workflow was inspected only; its existing contract is not part of this test.'
fi

echo 'non-minecraft-workflow-concurrency-test=passed'
