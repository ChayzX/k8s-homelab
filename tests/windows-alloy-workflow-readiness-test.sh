#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
workflow="$root/.github/workflows/windows-alloy-metrics-deploy.yml"

grep -q -- '--service-token-id "\$TUNNEL_SERVICE_TOKEN_ID"' "$workflow"
grep -q -- '--service-token-secret "\$TUNNEL_SERVICE_TOKEN_SECRET"' "$workflow"
grep -q 'cloudflared-access.pid' "$workflow"
grep -q 'cloudflared-access.log' "$workflow"
grep -q 'kill -0' "$workflow"
grep -q '/dev/tcp/127.0.0.1/16443' "$workflow"
grep -q 'if: always()' "$workflow"

echo 'windows-alloy-workflow-readiness-test=passed'
