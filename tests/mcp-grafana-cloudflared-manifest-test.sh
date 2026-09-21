#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$root/observability/mcp-grafana-cloudflared.yaml"

test -f "$manifest"
grep -q '^  name: mcp-grafana-cloudflared$' "$manifest"
grep -q '^      serviceAccountName: mcp-grafana-cloudflared-sa$' "$manifest"
grep -q '^                  name: mcp-grafana-cloudflared-tunnel-token$' "$manifest"
grep -q '^                  key: TUNNEL_TOKEN$' "$manifest"
grep -q '^            - name: TUNNEL_TRANSPORT_PROTOCOL$' "$manifest"
grep -q '^              value: http2$' "$manifest"
grep -q 'cloudflare/cloudflared:2026.7.3@sha256:' "$manifest"
grep -q '^      automountServiceAccountToken: false$' "$manifest"
grep -q '^            allowPrivilegeEscalation: false$' "$manifest"

# The one property this manifest exists to guarantee: this hostname's route
# must never be described as sitting behind Authentik, and must not
# reference any other hostname's secret or route.
if sed '/^[[:space:]]*#/d' "$manifest" | grep -Eiq '(authentik|oauth|mods\.|k8s-api|minecraft|ssh|rdp|commands-cloudflared|ci-tunnel-token)'; then
  echo 'dedicated mcp-grafana connector contains an unrelated route, secret reference, or an Authentik dependency' >&2
  exit 1
fi

# The setup doc must keep stating the Authentik-avoidance requirement so a
# future edit doesn't silently drop it.
doc="$root/observability/MCP-GRAFANA-SETUP.md"
test -f "$doc"
grep -qi 'Service Token' "$doc"
grep -qi 'not.*Authentik' "$doc"

echo 'mcp-grafana-cloudflared-manifest-test=passed'
