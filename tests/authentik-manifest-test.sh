#!/usr/bin/env bash
set -Eeuo pipefail

manifest=${1:-auth/50-ldap-outpost.yaml}
transport_manifest=${2:-auth/60-postgresql-transport.yaml}

for required_manifest in "$manifest" "$transport_manifest"; do
  test -f "$required_manifest"
done

grep -q '^apiVersion: apps/v1$' "$manifest"
grep -q '^  namespace: auth$' "$manifest"
grep -q '^  replicas: 1$' "$manifest"
grep -q '^      automountServiceAccountToken: false$' "$manifest"
grep -q '^          image: ghcr.io/goauthentik/ldap:2026.5.6$' "$manifest"
grep -q '^                  name: ldap-outpost-token$' "$manifest"
grep -q '^                  key: AUTHENTIK_TOKEN$' "$manifest"
grep -q '^  selector:$' "$manifest"
grep -q '^    matchLabels:$' "$manifest"
grep -q '^      app.kubernetes.io/name: ldap-outpost$' "$manifest"
grep -q '^      labels:$' "$manifest"
grep -q '^        app.kubernetes.io/name: ldap-outpost$' "$manifest"
grep -q '^            requests:$' "$manifest"
grep -q '^            limits:$' "$manifest"

grep -q '^apiVersion: v1$' "$transport_manifest"
grep -q '^kind: Service$' "$transport_manifest"
grep -q '^  name: auth-postgresql-transport$' "$transport_manifest"
grep -q '^  namespace: auth$' "$transport_manifest"
grep -q '^  type: NodePort$' "$transport_manifest"
grep -q '^  externalTrafficPolicy: Cluster$' "$transport_manifest"
grep -q '^    homelab/transport: encrypted$' "$transport_manifest"
grep -q '^    app.kubernetes.io/name: auth-postgresql-home-primary$' "$transport_manifest"
grep -q '^      port: 5432$' "$transport_manifest"
grep -q '^      targetPort: 5432$' "$transport_manifest"
grep -q '^      nodePort: 30433$' "$transport_manifest"

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

if grep -Eq '^[[:space:]]*(data:|stringData:)$' "$manifest" "$transport_manifest" ||
   grep -Eq '^[[:space:]]*(AUTHENTIK_TOKEN|token):[[:space:]]+[^[:space:]#]' "$manifest" "$transport_manifest"; then
  echo 'Authentik manifests must not contain plaintext secrets' >&2
  exit 1
fi

echo 'authentik-manifest-test=passed'
