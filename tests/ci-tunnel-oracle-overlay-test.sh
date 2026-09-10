#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
rendered=$(kubectl kustomize "$repo_root/ci-tunnel-oracle")

grep -q 'name: cloudflared-oracle' <<<"$rendered"
grep -q 'name: ci-tunnel-token-oracle' <<<"$rendered"
grep -q 'kubernetes.io/hostname: pantry-bot-oracle' <<<"$rendered"
grep -q '^  replicas: 0$' <<<"$rendered"
grep -q 'app.kubernetes.io/name: ci-tunnel-oracle' <<<"$rendered"

if grep -q 'name: ci-tunnel-token$' <<<"$rendered"; then
  echo 'oracle overlay references the home connector Secret' >&2
  exit 1
fi

home_rendered=$(kubectl kustomize "$repo_root/ci-tunnel")
grep -q 'name: cloudflared$' <<<"$home_rendered"
grep -q 'name: ci-tunnel-token$' <<<"$home_rendered"
grep -q '^  replicas: 1$' <<<"$home_rendered"

echo 'ci-tunnel-oracle-overlay-test=passed'
