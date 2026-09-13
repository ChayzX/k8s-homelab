#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dashboard="$root/dashboards/greenies-main-pc-health.json"

test -f "$dashboard"
jq -e '.title == "Greenie\u0027s Main PC Health"' "$dashboard" >/dev/null
jq -e '(.templating.list // []) | map(.name) | index("host") | not' "$dashboard" >/dev/null
jq -e '[.panels[] | .. | objects | .expr? // empty] | length >= 8' "$dashboard" >/dev/null
! jq -r '[.panels[] | .. | objects | .expr? // empty] | .[]' "$dashboard" | rg -v 'site="remote", job="host"'

echo 'greenies-main-pc-dashboard-test=passed'
