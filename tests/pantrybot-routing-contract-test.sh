#!/usr/bin/env bash
set -Eeuo pipefail

contract=${1:-docs/recovery/PANTRYBOT-PUBLIC-ROUTING.md}
test -f "$contract"

# OAuth must terminate at Authentik; the old direct PantryBot origin is not a
# valid public routing contract anymore.
grep -q 'oauth.greeniespantry.uk/\* -> http://auth-authentik-server.auth.svc.cluster.local:80' "$contract"
! grep -q 'oauth.greeniespantry.uk/\* -> http://pantry-bot.pantry-bot.svc:3000' "$contract"
grep -q 'Authentik login flow' "$contract"

# Viewer commands remain OAuth-free and the overlay remains a separate origin.
grep -q 'commands.greeniespantry.uk' "$contract"
grep -q 'overlay.greeniespantry.uk/\* -> http://pantry-overlay.pantry-bot.svc:8080' "$contract"

# The Cloudflare route adapter inputs must stay loadable and must not move OAuth
# away from Authentik. OAuth stays on the shared tunnel under the single
# home Authentik writer decision (#329).
adapter_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../observability/failover-witness" && pwd)
inputs="$adapter_dir/cloudflare-route-inputs.example.json"
test -f "$adapter_dir/cloudflare_route_adapter.py"
test -f "$inputs"
PYTHONPATH="$adapter_dir" python3 - "$inputs" <<'PY'
import json, sys
from cloudflare_route_adapter import load_inputs, site_tunnel_configs
path = sys.argv[1]
data = load_inputs(path)
if not data.get("account_id"):
    raise SystemExit("account_id is required")
for name, site in data["sites"].items():
    site_tunnel_configs(site)
oauth = [r for site in data["sites"].values() for r in site.get("application_routes", []) + site.get("commands_routes", [])
         if "oauth" in r.get("hostname", "")]
if oauth:
    raise SystemExit("route adapter inputs must not manage oauth (Authentik stays home)")
print("cloudflare-route-inputs=loadable")
PY

echo 'pantrybot-routing-contract-test=passed'
