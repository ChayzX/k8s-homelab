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

echo 'pantrybot-routing-contract-test=passed'
