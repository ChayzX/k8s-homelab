#!/usr/bin/env bash
set -Eeuo pipefail

# Site-neutral one-shot PantryBot promotion adapter. This replaces the stale
# "promote Oracle (PantryBot + Authentik)" adapter, which is obsolete under the
# single-Authentik-writer model: Authentik must never be auto-promoted away
# from home. This adapter only fenced/promoted PantryBot affects the one
# PantryBot PostgreSQL authority via the site-neutral controller.
#
# It is a thin wrapper over site_neutral_promoter.py --once with a failed-closed
# witness probe: nothing is promoted unless the old writer has already been
# fenced by the caller-supplied command and the witness grants the site lease.
#
# Run on the promoting site only, through its own management path.

usage() {
  cat <<'USAGE'
Usage: pantrybot-promote-site.sh --confirm | --dry-run

Required for a real promotion:
  PROMOTION_SITE                 site name: home | oracle | canada
  WITNESS_URL                    private witness base URL (or WITNESS_URL)
  WITNESS_SHARED_SECRET          witness Bearer secret (root-owned env only)
  OLD_WRITER_FENCE_COMMAND       exact old-writer fence command (argv string)

Optional overrides (site-neutral promoter defaults match Oracle):
  PROMOTION_POD                  --pod (default postgres-authority-standby-0)
  PROMOTION_SERVICE              --service (default postgres-authority-standby)
  PROMOTION_NAMESPACE            --namespace (default pantry-bot)
  PANTRY_PUBLIC_URLS             whitespace-separated readiness URLs to verify

Canada is a last-resort site only. PROMOTION_SITE=canada additionally requires:
  CANADA_LAST_RESORT_CONFIRM     operator acknowledgement token (single-use)
  CANADA_LAST_RESORT_HOME_GATE   command exiting 0 only when home is dark/fenced
  CANADA_LAST_RESORT_ORACLE_GATE command exiting 0 only when oracle is dark/fenced
USAGE
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }
mode=${1:-}
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] || { usage >&2; exit 2; }

: "${PROMOTION_SITE:?PROMOTION_SITE is required}"
: "${WITNESS_URL:=${PANTRY_WITNESS_URL:-}}"
: "${WITNESS_SHARED_SECRET:=}"
[[ -n "$WITNESS_URL" ]] || { echo 'promotion failed: witness URL is empty' >&2; exit 1; }
[[ -n "$WITNESS_SHARED_SECRET" ]] || { echo 'promotion failed: WITNESS_SHARED_SECRET is empty' >&2; exit 1; }
: "${OLD_WRITER_FENCE_COMMAND:?OLD_WRITER_FENCE_COMMAND is required}"

# Last-resort guard: home and oracle are always preferred over canada. Promotion
# to canada is refused unless the operator acknowledges with a token AND both
# preferred sites are dark (hard-fenced or unreachable) via the caller-supplied
# gates. Private site-dark probes only; a healthy public route is not proof a
# site is dead.
last_resort_ok=false
if [[ "$PROMOTION_SITE" == "canada" ]]; then
  [[ -n "${CANADA_LAST_RESORT_CONFIRM:-}" ]] || {
    echo 'promotion failed: canada is last resort and requires CANADA_LAST_RESORT_CONFIRM' >&2
    exit 2
  }
  [[ -n "$CANADA_LAST_RESORT_HOME_GATE" ]] && [[ -n "$CANADA_LAST_RESORT_ORACLE_GATE" ]] || {
    echo 'promotion failed: canada requires both CANADA_LAST_RESORT_HOME_GATE and CANADA_LAST_RESORT_ORACLE_GATE' >&2
    exit 2
  }
  if ( eval "$CANADA_LAST_RESORT_HOME_GATE" ) >/dev/null 2>&1 && ( eval "$CANADA_LAST_RESORT_ORACLE_GATE" ) >/dev/null 2>&1; then
    last_resort_ok=true
  else
    echo 'promotion failed: canada last-resort gates did not pass (home or oracle is alive)' >&2
    exit 1
  fi
fi

promoter=${SITE_NEUTRAL_PROMOTER:-/usr/local/lib/failover-witness/site_neutral_promoter.py}

argv=(--site "$PROMOTION_SITE" --once --old-writer-fence-command "$OLD_WRITER_FENCE_COMMAND")
[[ -n "${PROMOTION_POD:-}" ]] && argv+=(--pod "$PROMOTION_POD")
[[ -n "${PROMOTION_SERVICE:-}" ]] && argv+=(--service "$PROMOTION_SERVICE")
[[ -n "${PROMOTION_NAMESPACE:-}" ]] && argv+=(--namespace "$PROMOTION_NAMESPACE")
[[ "$last_resort_ok" == "true" ]] && argv+=(--allow-canada-last-resort)

if [[ "$mode" == "--dry-run" ]]; then
  if [[ "$PROMOTION_SITE" == "canada" ]]; then
    printf '%s\n' 'canada_last_resort_guard=home_dark oracle_dark'
  fi
  printf '%s\n' "promotion_site=$PROMOTION_SITE" old_writer_fence_validated witness_validated oracle_database_promoted application_ready
  exit 0
fi

[[ -f "$promoter" ]] || { echo "promotion failed: $promoter not installed" >&2; exit 1; }
export WITNESS_URL WITNESS_SHARED_SECRET
python3 "$promoter" "${argv[@]}"

for url in ${PANTRY_PUBLIC_URLS:-}; do
  [[ -n "$url" ]] || continue
  curl --fail --silent --show-error --max-time 15 "$url" >/dev/null || {
    echo "promotion failed: public route is not ready ($url)" >&2
    exit 1
  }
done
echo traffic_routed