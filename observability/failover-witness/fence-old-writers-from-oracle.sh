#!/usr/bin/env bash
set -Eeuo pipefail

# Composite old-writer fence used before Oracle promotion. Both independent
# writers must be positively fenced; a partial result is a hard failure.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || {
  echo 'old_writer_fence=failed reason=explicit_confirmation_required' >&2
  exit 2
}

"$SCRIPT_DIR/fence-home-direct-from-oracle.sh" "$mode"
"$SCRIPT_DIR/fence-canada-from-oracle.sh" "$mode"
echo "old_writer_fence=verified home=verified canada=verified"
