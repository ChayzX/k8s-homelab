#!/usr/bin/env bash
set -euo pipefail

# Verify the rendered JMusicBot Deployment's portable state contract without
# contacting Kubernetes, Discord, or R2. Usage:
#   kubectl kustomize jmusicbot | scripts/jmusicbot-r2-contract-check.sh

manifest="$(cat)"
python3 - "$manifest" <<'PY'
import re
import sys

manifest = sys.argv[1]

def require(fragment, explanation):
    if fragment not in manifest:
        raise SystemExit(f"missing {explanation}: {fragment}")

require("kind: Deployment", "JMusicBot Deployment")
require("name: jmusicbot", "JMusicBot name")
require("replicas: 1", "single rendered base replica")
require("type: Recreate", "single-writer rollout strategy")
require("r2:pantry-bot-backups/jmusicbot/", "approved R2 state prefix")
require("rclone copy r2:pantry-bot-backups/jmusicbot/ /musicbot/", "R2 restore command")
require("while true; do", "R2 sync loop")
require("if [ -f /musicbot/.jmusicbot-lease-owner ]; then", "ownership-gated R2 sync")
require("rclone sync /musicbot r2:pantry-bot-backups/jmusicbot/", "R2 sync command")
require("sleep 300", "five-minute sync interval")
require("JMUSICBOT_WITNESS_RESOURCE", "resource-scoped witness configuration")
if not re.search(r"name: JMUSICBOT_WITNESS_RESOURCE\s+value: jmusicbot", manifest):
    raise SystemExit("missing jmusicbot witness resource")

# The YAML uses quoted arguments in the restore command and shell words in the
# sidecar command. Require the lease marker on both directions independently.
if len(re.findall(r"--exclude [\x27\"]?\\?\.jmusicbot-lease-owner", manifest)) < 2:
    raise SystemExit("lease marker must be excluded from both restore and sync")
require("--exclude \'config.txt\'", "read-only config exclusion from R2 sync")
require("--exclude \'config.txt.bak*\'", "JMusicBot backup churn exclusion from R2 sync")

print("jmusicbot-r2-contract-check: passed")
PY
