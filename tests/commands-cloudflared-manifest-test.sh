#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$root/pantry-bot/62-deployment-commands-cloudflared.yaml"

test -f "$manifest"
grep -q '^  name: commands-cloudflared$' "$manifest"
grep -q '^      serviceAccountName: commands-cloudflared-sa$' "$manifest"
grep -q '^                  name: commands-cloudflared-tunnel-token$' "$manifest"
grep -q '^                  key: TUNNEL_TOKEN$' "$manifest"
grep -q '^            - name: TUNNEL_TRANSPORT_PROTOCOL$' "$manifest"
grep -q '^              value: http2$' "$manifest"
grep -q 'cloudflare/cloudflared:2026.7.3@sha256:' "$manifest"
grep -q '^      automountServiceAccountToken: false$' "$manifest"
grep -q '^            allowPrivilegeEscalation: false$' "$manifest"

if sed '/^[[:space:]]*#/d' "$manifest" | grep -Eq '(^|[[:space:]])(cloudflared-tunnel$|oauth|mods|k8s-api|minecraft|ssh|rdp)'; then
  echo 'dedicated commands connector contains an unrelated route or secret reference' >&2
  exit 1
fi

echo 'commands-cloudflared-manifest-test=passed'
