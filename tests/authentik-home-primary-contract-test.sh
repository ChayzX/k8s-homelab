#!/usr/bin/env bash
set -Eeuo pipefail

manifest=${1:-auth/home-postgresql-primary.yaml}
test -f "$manifest"

grep -q '^kind: Service$' "$manifest"
grep -q '^  name: auth-postgresql-home-primary$' "$manifest"
grep -q '^    app.kubernetes.io/name: auth-postgresql-home-primary$' "$manifest"
grep -q '^    app.kubernetes.io/part-of: authentik$' "$manifest"
grep -q '^    authentik.postgres/role: primary$' "$manifest"
grep -q '^kind: StatefulSet$' "$manifest"
grep -q '^  replicas: 1$' "$manifest"
grep -q '^      automountServiceAccountToken: false$' "$manifest"
grep -q '^        runAsNonRoot: true$' "$manifest"
grep -q '^          image: postgres:17.10-bookworm$' "$manifest"
grep -q '^                  name: auth-postgresql-home-primary$' "$manifest"
grep -q '^                  key: POSTGRES_DB$' "$manifest"
grep -q '^                  key: POSTGRES_USER$' "$manifest"
grep -q '^                  key: POSTGRES_PASSWORD$' "$manifest"
grep -q '^      nodeSelector:$' "$manifest"
grep -q '^        kubernetes.io/hostname: minecraftmachine$' "$manifest"
grep -q '^  volumeClaimTemplates:$' "$manifest"
grep -q '^        storageClassName: local-path$' "$manifest"

if grep -Eq '100\.78\.181\.15|100\.84\.89\.87|PRIMARY_HOST|REPLICATION_PASSWORD|initContainers:|type: NodePort' "$manifest"; then
  echo 'Home Authentik primary target must not contain remote, standby, or public transport wiring' >&2
  exit 1
fi

if grep -Eq '^[[:space:]]*(data:|stringData:)$|^[[:space:]]*(POSTGRES_DB|POSTGRES_USER|POSTGRES_PASSWORD):[[:space:]]+[^[:space:]#]' "$manifest"; then
  echo 'Home Authentik primary manifest must not contain plaintext secrets' >&2
  exit 1
fi

echo 'authentik-home-primary-contract-test=passed'
