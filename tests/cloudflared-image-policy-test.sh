#!/usr/bin/env bash
set -Eeuo pipefail

# Cloudflare connectors are externally reachable infrastructure. A mutable
# image tag can silently change the tunnel binary and bypass the reviewed
# rollout path, so every tracked connector manifest must use a digest.
manifests=(
  ci-tunnel/20-deployment.yaml
  pantry-bot/60-deployment-cloudflared.yaml
  pantry-bot/62-deployment-commands-cloudflared.yaml
  pantry-bot/66-deployment-app-cloudflared.yaml
)

for manifest in "${manifests[@]}"; do
  [[ -f "$manifest" ]] || {
    echo "missing connector manifest: $manifest" >&2
    exit 1
  }
  if rg -n 'image:\s*cloudflare/cloudflared:(latest|[0-9][^@[:space:]]*)\s*$' "$manifest"; then
    echo "connector image must be pinned by digest: $manifest" >&2
    exit 1
  fi
  rg -n 'image:\s*cloudflare/cloudflared:[^@[:space:]]+@sha256:[0-9a-f]{64}' "$manifest" >/dev/null || {
    echo "connector image digest missing: $manifest" >&2
    exit 1
  }
done

echo "cloudflared-image-policy-test=passed"
