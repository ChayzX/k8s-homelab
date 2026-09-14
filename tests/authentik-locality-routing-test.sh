#!/usr/bin/env bash
set -Eeuo pipefail

home_values=${1:-auth/HOME-VALUES.yaml}
oracle_values=${2:-auth/ORACLE-FAILOVER-VALUES.yaml}
routing_doc=${3:-docs/recovery/INTERACTIVE-ROUTE-OWNERSHIP.md}
return_manifest=${4:-auth/home-postgresql-return.yaml}
primary_manifest=${5:-auth/home-postgresql-primary.yaml}

for path in "$home_values" "$oracle_values" "$routing_doc" "$return_manifest" "$primary_manifest"; do
  test -f "$path" || { echo "missing required contract: $path" >&2; exit 1; }
done

grep -q 'host: auth-postgresql$' "$home_values"
grep -q 'host: auth-postgresql-standby.auth.svc.cluster.local$' "$oracle_values"
grep -q '^Normal owner: Home$' "$routing_doc"
grep -q '^Promotion owner: Oracle$' "$routing_doc"
grep -q '^  name: auth-postgresql-home-return$' "$return_manifest"
grep -q 'kubernetes.io/hostname: minecraftmachine' "$return_manifest"
grep -q 'PRIMARY_SLOT_NAME' "$return_manifest"
grep -q '^  name: auth-postgresql-home-primary$' "$primary_manifest"
grep -q 'auth-postgresql-home-primary$' "$primary_manifest"
grep -q 'kubernetes.io/hostname: minecraftmachine' "$primary_manifest"
grep -q '`auth.greeniespantry.uk`: Home' "$routing_doc"
grep -q '`oauth.greeniespantry.uk`: Home' "$routing_doc"
grep -q '`grafana.greeniespantry.uk`: Home' "$routing_doc"
grep -q '`operations.greeniespantry.uk`: Home' "$routing_doc"

if grep -Eq '100\.78\.181\.15|100\.84\.89\.87' "$home_values"; then
  echo 'Home Authentik values must not use a remote database endpoint' >&2
  exit 1
fi

echo 'authentik-locality-routing-test=passed'
