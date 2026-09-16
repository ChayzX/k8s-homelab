#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
adapter="$root/scripts/pantrybot-promote-site.sh"
publisher="$root/observability/failover-witness/publish-cloudflare-routes.sh"
promoter="$root/observability/failover-witness/site_neutral_promoter.py"

require() {
  local fragment="$1"
  local file="$2"
  if ! grep -Fq -- "$fragment" "$file"; then
    echo "pantrybot-last-resort-guard-test: missing in ${file##*/}: $fragment" >&2
    exit 1
  fi
}

#{ promote-site.sh hard-fails for canada without a token }

home_gate_fail='exit 1'
oracle_gate_fail='exit 1'
home_gate_dark='exit 0'
oracle_gate_dark='exit 0'
base_env=(PROMOTION_SITE=canada WITNESS_URL=http://127.0.0.1:8765 WITNESS_SHARED_SECRET=t OLD_WRITER_FENCE_COMMAND="true")

out=$(env "${base_env[@]}" "$adapter" --dry-run 2>&1 || true)
case "$out" in *CANADA_LAST_RESORT_CONFIRM*) ;; *) echo "gate: canada without token did not refuse" >&2; exit 1 ;; esac

out=$(env "${base_env[@]}" CANADA_LAST_RESORT_CONFIRM=t \
  CANADA_LAST_RESORT_HOME_GATE="$home_gate_fail" CANADA_LAST_RESORT_ORACLE_GATE="$oracle_gate_dark" \
  "$adapter" --dry-run 2>&1 || true)
case "$out" in *last-resort\ gates\ did\ not\ pass*) ;; *) echo "gate: canada with live home did not refuse" >&2; exit 1 ;; esac

out=$(env "${base_env[@]}" CANADA_LAST_RESORT_CONFIRM=t \
  CANADA_LAST_RESORT_HOME_GATE="$home_gate_dark" CANADA_LAST_RESORT_ORACLE_GATE="$oracle_gate_dark" \
  "$adapter" --dry-run 2>&1 || true)
case "$out" in *canada_last_resort_guard=home_dark\ oracle_dark*) ;; *) echo "gate: canada with both sites dark did not proceed" >&2; exit 1 ;; esac

#{ oracle needs no last-resort token (home+oracle preferred; canada only gated) }

out=$(env PROMOTION_SITE=oracle WITNESS_URL=http://127.0.0.1:8765 WITNESS_SHARED_SECRET=t OLD_WRITER_FENCE_COMMAND="true" \
  "$adapter" --dry-run 2>&1 || true)
case "$out" in *promotion_site=oracle*) ;; *) echo "gate: oracle promotion was not token-free" >&2; exit 1 ;; esac

#{ publisher: canada requires token; oracle does not }

pub_out=$(env PANTRY_PROMOTION_SITE=canada bash "$publisher" 2>&1 || true)
case "$pub_out" in *requires\ CANADA_LAST_RESORT_CONFIRM*) ;; *) echo "gate: publisher canada without token did not refuse" >&2; exit 1 ;; esac
pub_orc=$(env PANTRY_PROMOTION_SITE=oracle bash "$publisher" 2>&1 || true)
case "$pub_orc" in *requires\ CANADA_LAST_RESORT_CONFIRM*) echo "gate: publisher oracle was refused" >&2; exit 1 ;; esac

#{ source-level contracts }

require 'CANADA_LAST_RESORT_CONFIRM' "$adapter"
require 'CANADA_LAST_RESORT_HOME_GATE' "$adapter"
require 'CANADA_LAST_RESORT_ORACLE_GATE' "$adapter"
require '--allow-canada-last-resort' "$adapter"
require 'allow_canada_last_resort' "$promoter"
require 'refused (--once required)' "$promoter"
require 'unit must NEVER be installed or' "$root/observability/failover-witness/pantry-postgres-promoter@.service.template"

echo "pantrybot-last-resort-guard-test=passed"