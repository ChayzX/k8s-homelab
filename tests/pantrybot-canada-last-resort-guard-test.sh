#!/usr/bin/env bash
set -Eeuo pipefail

# CI gate: Canada must never be promotable without an explicit operator
# token AND both preferred sites independently proven dark. This never
# touches real SSH/Docker/Kubernetes; it only proves the refusal paths.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../observability/failover-witness" && pwd)"
PROMOTE="$SCRIPT_DIR/canada-last-resort-promote.sh"
PUBLISH="$SCRIPT_DIR/publish-cloudflare-routes.sh"

fail() { echo "pantrybot-canada-last-resort-guard-test=failed reason=$1" >&2; exit 1; }

# 1. Refuses without CANADA_LAST_RESORT_CONFIRM at all.
if env -u CANADA_LAST_RESORT_CONFIRM "$PROMOTE" --dry-run >/tmp/canada-guard-1.out 2>&1; then
  fail "promote_succeeded_without_confirm_token"
fi
grep -q CANADA_LAST_RESORT_CONFIRM /tmp/canada-guard-1.out || fail "missing_confirm_token_error_unclear"

# 2. Refuses when the home gate does not pass, even with a token and a
# (fake) passing oracle gate -- never reaches SSH to Canada.
if CANADA_LAST_RESORT_CONFIRM=test \
   CANADA_ADDRESS=100.104.83.28 \
   CANADA_ADMIN_KEY=/dev/null \
   CANADA_LAST_RESORT_HOME_GATE=false \
   CANADA_LAST_RESORT_ORACLE_GATE=true \
   "$PROMOTE" --dry-run >/tmp/canada-guard-2.out 2>&1; then
  fail "promote_succeeded_with_home_not_dark"
fi
grep -q 'home is not confirmed dark' /tmp/canada-guard-2.out || fail "home_gate_failure_not_reported"

# 3. Refuses when the oracle gate does not pass, even with a token and a
# passing home gate.
if CANADA_LAST_RESORT_CONFIRM=test \
   CANADA_ADDRESS=100.104.83.28 \
   CANADA_ADMIN_KEY=/dev/null \
   CANADA_LAST_RESORT_HOME_GATE=true \
   CANADA_LAST_RESORT_ORACLE_GATE=false \
   "$PROMOTE" --dry-run >/tmp/canada-guard-3.out 2>&1; then
  fail "promote_succeeded_with_oracle_not_dark"
fi
grep -q 'oracle is not confirmed dark' /tmp/canada-guard-3.out || fail "oracle_gate_failure_not_reported"

# 4. Refuses an unexpected CANADA_ADDRESS even with everything else set,
# so a copy-pasted/misconfigured address can never silently target the
# wrong host.
if CANADA_LAST_RESORT_CONFIRM=test \
   CANADA_ADDRESS=192.0.2.1 \
   CANADA_ADMIN_KEY=/dev/null \
   CANADA_LAST_RESORT_HOME_GATE=true \
   CANADA_LAST_RESORT_ORACLE_GATE=true \
   "$PROMOTE" --dry-run >/tmp/canada-guard-4.out 2>&1; then
  fail "promote_succeeded_with_unexpected_address"
fi

# 5. publish-cloudflare-routes.sh refuses a canada route publish without
# the same token.
if env -u CANADA_LAST_RESORT_CONFIRM PANTRY_PROMOTION_SITE=canada "$PUBLISH" >/tmp/canada-guard-5.out 2>&1; then
  fail "publish_succeeded_without_confirm_token"
fi
grep -q CANADA_LAST_RESORT_CONFIRM /tmp/canada-guard-5.out || fail "publish_missing_confirm_token_error_unclear"

rm -f /tmp/canada-guard-*.out
echo 'pantrybot-canada-last-resort-guard-test=passed'
