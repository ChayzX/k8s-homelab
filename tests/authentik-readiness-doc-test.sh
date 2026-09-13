#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DOC="$ROOT/docs/recovery/AUTHENTIK-HA-READINESS.md"

test -f "$DOC"

# Application capacity may be present at both sites, but the identity database
# must remain one-writer-per-epoch until promotion/fencing is proven.
grep -Fq 'Home and Oracle have Authentik server, worker, and LDAP application' "$DOC"
grep -Fq 'Oracle application replicas use the home PostgreSQL authority' "$DOC"
grep -Fq 'one writable PostgreSQL primary per fencing' "$DOC"
grep -Fq 'old-writer rejection' "$DOC"
grep -Fq 'controlled rollback' "$DOC"

if grep -Fq 'Oracle application replicas remain scaled to zero' "$DOC"; then
  echo 'readiness record contains stale zero-replica Oracle application claim' >&2
  exit 1
fi

echo 'authentik-readiness-doc-test=passed'
