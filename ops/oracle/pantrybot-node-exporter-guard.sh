#!/bin/sh
set -eu

NFT=/usr/sbin/nft
RULES=/etc/pantrybot-node-exporter-guard.nft

case "${1:-}" in
  start)
    # Delete and recreate in one nft batch so an error preserves the old table
    # and there is no interval where the exporter is exposed.
    BATCH=$(mktemp)
    trap 'rm -f "$BATCH"' EXIT HUP INT TERM
    if "$NFT" list table inet pantrybot_guard >/dev/null 2>&1; then
      printf '%s\n' 'delete table inet pantrybot_guard' >"$BATCH"
      cat "$RULES" >>"$BATCH"
    else
      cp "$RULES" "$BATCH"
    fi
    "$NFT" -f "$BATCH"
    ;;
  stop)
    # Fail closed: stopping the unit must not reopen node-exporter.
    exit 0
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
