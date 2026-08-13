#!/usr/bin/env bash
# k8s-homelab-8nc, option (a): long-term host-alive history in Uptime Kuma.
#
# Kuma stores up/down history per monitor, not metric series -- this gives
# you >7d "was the host up" history (Grafana's Prometheus/Loki retention is
# 7d by design, see dashboards/README.md's host-health section), NOT CPU/mem
# trends beyond 7d. A real long-term trend store is a separate, bigger
# decision (remote-write to a TSDB) -- not what this script does.
#
# Setup (one-time, manual -- the push token can't be generated any other way):
#   1. Uptime Kuma UI (https://status.greeniespantry.uk) -> Add New Monitor
#      -> Monitor Type: Push -> name it e.g. "Host heartbeat" -> Heartbeat
#      Interval 60s -> Retries/Resend as you like -> Save.
#   2. Kuma shows the push URL, e.g.
#      https://status.greeniespantry.uk/api/push/<TOKEN>?status=up&msg=OK&ping=
#      Copy just the <TOKEN> segment.
#   3. Put ONLY the token in this file (never commit it):
#        echo 'KUMA_PUSH_TOKEN=<token>' > ~/.config/kuma-heartbeat.env
#        chmod 600 ~/.config/kuma-heartbeat.env
#   4. Crontab (every minute; monitor interval above should be 2x this or
#      more so one missed run doesn't flip it down):
#        * * * * * /home/chase/k8s-homelab/scripts/kuma-host-heartbeat.sh >> /home/chase/k8s-homelab/scripts/kuma-heartbeat.log 2>&1
set -euo pipefail

ENV_FILE="${HOME}/.config/kuma-heartbeat.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "$(date -Is) missing $ENV_FILE -- see this script's header for setup" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -z "${KUMA_PUSH_TOKEN:-}" ]]; then
  echo "$(date -Is) KUMA_PUSH_TOKEN not set in $ENV_FILE" >&2
  exit 1
fi

curl -fsS -m 10 --retry 2 \
  "https://status.greeniespantry.uk/api/push/${KUMA_PUSH_TOKEN}?status=up&msg=host-alive" \
  -o /dev/null
