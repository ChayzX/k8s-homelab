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
# Exit 75 (accepted by the composite as fenced-by-lease-expiry) only when
# ssh never reached Canada at all. ssh uses 255 for every ssh-level error,
# including auth and host-key failures, which mean the host IS up - those,
# connection refused, and any remote-side failure stay hard failures.
run_remote() {
  local errf rc
  errf="$(mktemp)"
  set +e
  "${ssh_args[@]}" "$@" 2>"$errf"
  rc=$?
  set -e
  cat "$errf" >&2
  if [[ "$rc" -eq 255 ]] \
    && grep -qiE 'timed out|no route to host|network is unreachable' "$errf" \
    && ! grep -qiE 'refused|permission denied|host key|authentication' "$errf"; then
    rm -f "$errf"
    echo 'canada_writer_fence=unreachable' >&2
    exit 75
  fi
  rm -f "$errf"
  return "$rc"
}

if [[ "$mode" == "--dry-run" ]]; then
  run_remote powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -Command "if (-not (Test-Path -LiteralPath '$CANADA_FENCE_SCRIPT')) { exit 1 }"
  echo 'canada_writer_fence=dry-run transport=verified'
  exit 0
fi

run_remote powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File "$CANADA_FENCE_SCRIPT" -ConfirmFence

echo 'canada_writer_fence=verified'
