#!/usr/bin/env bash
set -Eeuo pipefail
# Non-production acceptance checks. Refuses production resources by default.
: "${PANTRY_REHEARSAL_DATABASE_URL:?Set an isolated rehearsal database URL}"
case "$PANTRY_REHEARSAL_DATABASE_URL" in *prod*|*production*) echo 'Refusing production-looking database URL' >&2; exit 2;; esac
: "${PANTRY_WITNESS_URL:?Set the rehearsal witness URL}"
: "${PANTRY_WITNESS_SECRET:?Set the rehearsal witness secret}"
RESOURCE="${PANTRY_REHEARSAL_RESOURCE:-pantry:rehearsal:$(hostname)}"
SITE="${PANTRY_REHEARSAL_SITE:-canada}"
base="${PANTRY_WITNESS_URL%/}"
json=$(curl --fail-with-body --silent --show-error --max-time 5 -H "Authorization: Bearer $PANTRY_WITNESS_SECRET" -H 'Content-Type: application/json' -d "{\"site\":\"$SITE\",\"resource\":\"$RESOURCE\"}" "$base/v1/authority/acquire")
epoch=$(printf '%s' "$json" | jq -er '.epoch')
token=$(printf '%s' "$json" | jq -er '.token')
renew=$(curl --fail-with-body --silent --show-error --max-time 5 -H "Authorization: Bearer $PANTRY_WITNESS_SECRET" -H 'Content-Type: application/json' -d "{\"site\":\"$SITE\",\"resource\":\"$RESOURCE\",\"epoch\":$epoch,\"token\":\"$token\"}" "$base/v1/authority/renew")
printf '%s\n' "$renew" | jq -e --argjson e "$epoch" '.ok == true and .epoch == $e' >/dev/null
echo "PASS witness acquire+renew resource=$RESOURCE site=$SITE epoch=$epoch"
# Read-only route checks; configure expected markers for each public origin.
for spec in ${PANTRY_ROUTE_CHECKS:-}; do
  url=${spec%%=*}; marker=${spec#*=}
  body=$(curl --fail-with-body --silent --show-error --max-time 10 "$url")
  printf '%s' "$body" | grep -F -- "$marker" >/dev/null || { echo "FAIL route marker $url" >&2; exit 1; }
  echo "PASS route $url marker=$marker"
done
# Application acceptance is delegated to disposable-channel tests; never send live traffic here.
echo 'PASS no live Twitch side effects requested'
