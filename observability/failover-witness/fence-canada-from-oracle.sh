#!/usr/bin/env bash
set -euo pipefail

# Out-of-band hook for Oracle promotion. The caller must provide the immutable
# Canada Tailscale address and the dedicated admin key through root-owned env.
: "${CANADA_ADDRESS:?CANADA_ADDRESS is required}"
: "${CANADA_ADMIN_KEY:?CANADA_ADMIN_KEY is required}"
: "${CANADA_FENCE_SCRIPT:?CANADA_FENCE_SCRIPT is required}"

mode="${1:-}"
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] && [[ "$#" == 1 ]] || {
  echo 'canada_writer_fence=failed reason=explicit_confirmation_required' >&2
  exit 2
}

case "$CANADA_ADDRESS" in
  100.104.83.28) ;;
  *) echo 'refusing unexpected Canada identity' >&2; exit 1 ;;
esac

ssh_args=( /usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes \
  -o ConnectTimeout=10 -i "$CANADA_ADMIN_KEY" "BotAdmin@$CANADA_ADDRESS" \
)
if [[ "$mode" == "--dry-run" ]]; then
  "${ssh_args[@]}" powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -Command "if (-not (Test-Path -LiteralPath '$CANADA_FENCE_SCRIPT')) { exit 1 }"
  echo 'canada_writer_fence=dry-run transport=verified'
  exit 0
fi

"${ssh_args[@]}" powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File "$CANADA_FENCE_SCRIPT" -ConfirmFence

echo 'canada_writer_fence=verified'
