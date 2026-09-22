#!/usr/bin/env bash
set -Eeuo pipefail

# Acquire gate for a site's promoter (#191): exit 0 = this site may try to
# take the pantry:postgres lease now, 1 = not its turn / not safe.
#
# Priority without preemption: a standby may only compete after its upstream
# has been gone for PRIORITY_DELAY_SECONDS (home 0, oracle 45, canada 120).
# If a higher-priority site promotes first, this site's follower re-points to
# it, streaming resumes, and the gate closes again.
# Freshness: the standby must have streamed within MAX_STALENESS_SECONDS and
# carry the expected system identifier, so a long-disconnected (stale) replica
# never promotes. A primary always passes (resume after restart; the promoter
# validates the generation itself). Any unreadable/old state fails closed.
: "${STATE_READ_COMMAND:?command printing the follower state file}"
DELAY="${PRIORITY_DELAY_SECONDS:?}"
MAX_STALE="${MAX_STALENESS_SECONDS:-600}"
MAX_SAMPLE_AGE="${MAX_SAMPLE_AGE_SECONDS:-30}"
EXPECTED_SYSID="${EXPECTED_SYSTEM_IDENTIFIER:?}"
NOW="${GATE_NOW:-$(date +%s)}"

closed() { echo "acquire_gate=closed reason=$1" >&2; exit 1; }

read -r -a cmd <<<"$STATE_READ_COMMAND"
state="$("${cmd[@]}" 2>/dev/null)" || closed state_unreadable
get() { sed -n "s/^$1=//p" <<<"$state" | head -1; }

sampled="$(get sampled_at)"; role="$(get role)"
[[ "$sampled" =~ ^[0-9]+$ ]] || closed no_sample
(( NOW - sampled <= MAX_SAMPLE_AGE )) || closed "follower_stale:$((NOW - sampled))s"
if [[ "$role" == primary ]]; then echo "acquire_gate=open role=primary"; exit 0; fi
[[ "$role" == standby ]] || closed "role:${role:-unknown}"
[[ "$(get system_identifier)" == "$EXPECTED_SYSID" ]] || closed system_identifier_mismatch
[[ "$(get streaming)" == 0 ]] || closed upstream_alive
since="$(get disconnected_since)"; last="$(get last_streaming)"
[[ "$since" =~ ^[0-9]+$ ]] || closed no_disconnect_time
(( NOW - since >= DELAY )) || closed "priority_wait:$((DELAY - (NOW - since)))s"
[[ "$last" =~ ^[0-9]+$ ]] || closed never_streamed
(( NOW - last <= MAX_STALE )) || closed "stale_replica:$((NOW - last))s"
echo "acquire_gate=open role=standby disconnected=$((NOW - since))s last_streaming_age=$((NOW - last))s"
