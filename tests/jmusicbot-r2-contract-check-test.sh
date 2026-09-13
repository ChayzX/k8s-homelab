#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
checker="$root/scripts/jmusicbot-r2-contract-check.sh"
rendered="$(mktemp)"
invalid="$(mktemp)"
trap 'rm -f "$rendered" "$invalid"' EXIT

kubectl kustomize "$root/jmusicbot" >"$rendered"
"$checker" <"$rendered"

# Removing the lease guard must invalidate the contract: an unowned pod must
# never be able to synchronize its restored state back to the shared prefix.
sed "/--exclude '.jmusicbot-lease-owner'/d" "$rendered" >"$invalid"
if "$checker" <"$invalid" >/dev/null 2>&1; then
  echo "jmusicbot-r2-contract-check-test: checker accepted missing lease exclusion" >&2
  exit 1
fi

echo "jmusicbot-r2-contract-check-test: all assertions passed"
