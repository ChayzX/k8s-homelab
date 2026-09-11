#!/usr/bin/env bash
set -Eeuo pipefail

manifest=${1:-auth/50-ldap-outpost.yaml}

grep -q '^kind: Deployment$' "$manifest"
grep -q '^  name: ldap-outpost$' "$manifest"
grep -q '^      automountServiceAccountToken: false$' "$manifest"
grep -q 'image: ghcr.io/goauthentik/ldap:2026.5.6$' "$manifest"
grep -q 'name: ldap-outpost-token$' "$manifest"
grep -q 'key: AUTHENTIK_TOKEN$' "$manifest"
grep -q '^            requests:$' "$manifest"
grep -q '^            limits:$' "$manifest"
grep -q '^kind: Service$' "$manifest"
grep -q '^      port: 389$' "$manifest"
grep -q '^      port: 636$' "$manifest"

if grep -Eq '(^|[[:space:]])(stringData:|token:|AUTHENTIK_TOKEN:[[:space:]]+[^$])' "$manifest"; then
  echo 'LDAP outpost manifest must not contain a plaintext token' >&2
  exit 1
fi

echo 'authentik-manifest-test=passed'
