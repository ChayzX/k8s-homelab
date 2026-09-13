#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
checker="$root/scripts/jmusicbot-r2-contract-check.sh"
rendered="$(mktemp)"
invalid="$(mktemp)"
trap 'rm -f "$rendered" "$invalid"' EXIT

if [[ -f "$root/jmusicbot/kustomization.yaml" || -f "$root/jmusicbot/kustomization.yml" ]]; then
  kubectl kustomize "$root/jmusicbot" >"$rendered"
else
  # This repository's JMusicBot manifests are intentionally ordered raw YAML;
  # preserve filename order when constructing the contract input.
  cat "$root"/jmusicbot/*.yaml >"$rendered"
fi
"$checker" <"$rendered"

# Removing the lease guard must invalidate the contract: an unowned pod must
# never be able to synchronize its restored state back to the shared prefix.
sed "/--exclude '.jmusicbot-lease-owner'/d" "$rendered" >"$invalid"
if "$checker" <"$invalid" >/dev/null 2>&1; then
  echo "jmusicbot-r2-contract-check-test: checker accepted missing lease exclusion" >&2
  exit 1
fi

echo "jmusicbot-r2-contract-check-test: all assertions passed"
