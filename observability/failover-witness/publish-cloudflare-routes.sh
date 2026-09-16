#!/usr/bin/env bash
set -Eeuo pipefail

: "${PANTRY_PROMOTION_SITE:?PANTRY_PROMOTION_SITE is required}"
if [[ "$PANTRY_PROMOTION_SITE" != "canada" && "$PANTRY_PROMOTION_SITE" != "oracle" ]]; then
  echo "unsupported promotion site" >&2
  exit 2
fi

exec /usr/bin/python3 /usr/local/lib/failover-witness/cloudflare_route_adapter.py \
  --inputs /etc/failover-witness/cloudflare-route-inputs.json \
  --active "$PANTRY_PROMOTION_SITE" --apply
