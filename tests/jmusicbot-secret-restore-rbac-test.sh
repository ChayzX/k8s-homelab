#!/usr/bin/env bash
set -euo pipefail

manifest="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docs/recovery/jmusicbot-secret-restore-rbac.yaml"

test -s "$manifest"
grep -Fq 'kind: ServiceAccount' "$manifest"
grep -Fq 'kind: Role' "$manifest"
grep -Fq 'kind: RoleBinding' "$manifest"
grep -Fq 'resourceNames:' "$manifest"
grep -Fq -- '- jmusicbot-config-txt' "$manifest"
grep -Fq -- '- jmusicbot-r2' "$manifest"
grep -Fq 'verbs: ["get", "update", "patch"]' "$manifest"

# The recovery identity must never gain discovery, creation, deletion, or
# arbitrary-secret read access through this manifest.
if grep -Eq 'verbs:.*(list|watch|create|delete)' "$manifest"; then
  echo 'secret-restore-rbac-test=failed: broad verb detected' >&2
  exit 1
fi
if grep -Eq '^[[:space:]]*-[[:space:]]+secrets[[:space:]]*$' "$manifest"; then
  echo 'secret-restore-rbac-test=failed: resourceNames scope missing' >&2
  exit 1
fi

echo 'secret-restore-rbac-test=passed'
