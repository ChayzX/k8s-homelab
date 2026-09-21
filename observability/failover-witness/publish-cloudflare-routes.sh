#!/usr/bin/env bash
set -Eeuo pipefail

: "${PANTRY_PROMOTION_SITE:?PANTRY_PROMOTION_SITE is required}"
if [[ "$PANTRY_PROMOTION_SITE" != "canada" && "$PANTRY_PROMOTION_SITE" != "oracle" ]]; then
  echo "unsupported promotion site" >&2
  exit 2
fi
# Canada is last-resort only: require the same single-use operator
# acknowledgement token the promotion path itself requires, so a route
# cannot be pointed at Canada by any path that skipped the dual dark-site
# gate (see canada-last-resort-promote.sh).
if [[ "$PANTRY_PROMOTION_SITE" == "canada" && -z "${CANADA_LAST_RESORT_CONFIRM:-}" ]]; then
  echo "refusing canada route publish without CANADA_LAST_RESORT_CONFIRM" >&2
  exit 2
fi

exec /usr/bin/python3 /usr/local/lib/failover-witness/cloudflare_route_adapter.py \
  --inputs /etc/failover-witness/cloudflare-route-inputs.json \
  --active "$PANTRY_PROMOTION_SITE" --apply
