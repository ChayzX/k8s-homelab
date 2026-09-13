#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
home="$ROOT/auth/authentik-home-values.example.yaml"
oracle="$ROOT/auth/authentik-oracle-values.example.yaml"

test -f "$home"
test -f "$oracle"
grep -Fq 'kubernetes.io/hostname: minecraftmachine' "$home"
grep -Fq 'kubernetes.io/hostname: pantry-bot-oracle' "$oracle"
grep -Eq '^server:' "$home"
grep -Eq '^worker:' "$home"
grep -Eq '^server:' "$oracle"
grep -Eq '^worker:' "$oracle"

if grep -Eiq '(^|[[:space:]])(password|token|secret|database_url|postgresql):' "$home" "$oracle"; then
  echo 'authentik-placement-contract-test=failed: secret/database setting found' >&2
  exit 1
fi

grep -Fq 'failed low-limit experiment' "$home"
grep -Fq 'fenced PostgreSQL authority' "$oracle"
echo 'authentik-placement-contract-test=passed'
