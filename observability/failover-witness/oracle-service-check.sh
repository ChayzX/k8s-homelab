#!/usr/bin/env bash
set -Eeuo pipefail
for url in "${PANTRY_SERVICE_CHECK_URLS:-http://127.0.0.1:13100/ready,http://127.0.0.1:18080/ready,http://127.0.0.1:18081/ready}"; do
  curl --fail --silent --show-error --max-time 5 "$url" >/dev/null || { echo "service_check=failed url=$url" >&2; exit 1; }
done
echo 'service_check=passed'
