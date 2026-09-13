#!/usr/bin/env bash
set -Eeuo pipefail

# Production Oracle promotion adapter for PantryBot. Run this on Oracle (or
# through its management path), never from the home control plane. The home
# fence command must be an independently reachable adapter; witness authority
# alone is not sufficient.

usage() {
  cat <<'USAGE'
Usage: pantrybot-promote-oracle.sh --confirm | --dry-run

Required for a real promotion:
  PANTRY_WITNESS_URL          private witness base URL (or WITNESS_URL)
  PANTRY_WITNESS_SECRET_FILE  root-readable file containing witness secret
                              (or WITNESS_SHARED_SECRET in the environment)
  HOME_FENCE_COMMAND          exact home fence adapter path
  ORACLE_KUBECTL               kubectl binary or wrapper for the Oracle cluster
  PANTRY_PUBLIC_URLS           whitespace-separated public readiness URLs
USAGE
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }
mode=${1:-}
[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]] || { usage >&2; exit 2; }

: "${PANTRY_WITNESS_URL:=${WITNESS_URL:-}}"
: "${PANTRY_WITNESS_SECRET_FILE:=}"
: "${PANTRY_WITNESS_SHARED_SECRET:=${WITNESS_SHARED_SECRET:-}}"
: "${HOME_FENCE_COMMAND:?HOME_FENCE_COMMAND is required}"
: "${ORACLE_KUBECTL:?ORACLE_KUBECTL is required}"
: "${PANTRY_PUBLIC_URLS:?PANTRY_PUBLIC_URLS is required}"
[[ -n "$PANTRY_WITNESS_URL" ]] || { echo 'promotion failed: witness URL is empty' >&2; exit 1; }

if [[ "$mode" == "--dry-run" ]]; then
  printf '%s\n' authority_acquired source_fenced database_promoted database_ready application_ready traffic_routed
  exit 0
fi

if [[ -n "$PANTRY_WITNESS_SECRET_FILE" ]]; then
  [[ -r "$PANTRY_WITNESS_SECRET_FILE" ]] || { echo 'promotion failed: witness secret file is unreadable' >&2; exit 1; }
  secret=$(<"$PANTRY_WITNESS_SECRET_FILE")
else
  secret="$PANTRY_WITNESS_SHARED_SECRET"
fi
[[ -n "$secret" ]] || { echo 'promotion failed: witness secret is empty' >&2; exit 1; }

KUBECTL_BIN="$ORACLE_KUBECTL"
k() { "$KUBECTL_BIN" "$@"; }
ns_pantry() { k -n pantry-bot "$@"; }
ns_auth() { k -n auth "$@"; }

# The fence is supplied as a root-owned, operator-reviewed command line. Parse
# it into argv so SSH options are supported without invoking a shell.
read -r -a home_fence_command <<< "$HOME_FENCE_COMMAND"
(( ${#home_fence_command[@]} > 0 )) || { echo 'promotion failed: home fence command is empty' >&2; exit 1; }

authority=$(curl --fail-with-body --silent --show-error --max-time 8 \
  -X POST "$PANTRY_WITNESS_URL/v1/authority/acquire" \
  -H "Authorization: Bearer $secret" -H 'Content-Type: application/json' \
  --data '{"site":"oracle","resource":"pantry"}') || {
  echo 'promotion failed: witness authority unavailable' >&2; exit 1;
}
epoch=$(jq -er '.epoch | select(type == "number" and . >= 1)' <<<"$authority") || {
  echo 'promotion failed: invalid witness epoch' >&2; exit 1;
}
token=$(jq -er '.token | select(type == "string" and length >= 16)' <<<"$authority") || {
  echo 'promotion failed: invalid witness token' >&2; exit 1;
}
[[ "$(jq -r '.site' <<<"$authority")" == oracle ]] || {
  echo 'promotion failed: witness granted a non-Oracle site' >&2; exit 1;
}
printf 'authority_acquired epoch=%s\\n' "$epoch"

"${home_fence_command[@]}" --confirm >/dev/null
echo source_fenced

ns_pantry get pod postgres-authority-standby-0 >/dev/null
recovery=$(ns_pantry exec postgres-authority-standby-0 -- sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "select pg_is_in_recovery()"' | tr -d '\r')
[[ "$recovery" == t ]] || { echo 'promotion failed: Oracle is not a standby' >&2; exit 1; }
ns_pantry exec postgres-authority-standby-0 -- su postgres -s /bin/sh -c \
  'pg_ctl -D /var/lib/postgresql/data promote' >/dev/null
for _ in $(seq 1 60); do
  recovery=$(ns_pantry exec postgres-authority-standby-0 -- sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "select pg_is_in_recovery()"' | tr -d '\r')
  [[ "$recovery" == f ]] && break
  sleep 1
done
[[ "${recovery:-}" == f ]] || { echo 'promotion failed: Oracle database did not promote' >&2; exit 1; }
echo database_promoted

ns_pantry label pod postgres-authority-standby-0 pantrybot.postgres/role=primary --overwrite >/dev/null
ns_pantry patch service postgres-authority-standby --type=json \
  -p='[{"op":"replace","path":"/spec/selector/pantrybot.postgres~1role","value":"primary"}]' >/dev/null
echo database_ready

# Point application roles at the promoted local authority without printing or
# reconstructing any secret values in logs.
pantry_url_b64=$(ns_pantry get secret pantry-bot-platform -o jsonpath='{.data.PANTRY_DATABASE_URL}')
pantry_url=$(printf '%s' "$pantry_url_b64" | base64 -d)
local_url=$(printf '%s' "$pantry_url" | sed -E 's#@[^/]+/#@postgres-authority-standby.pantry-bot.svc.cluster.local:5432/#')
local_url_b64=$(printf '%s' "$local_url" | base64 -w0)
patch=$(jq -cn --arg v "$local_url_b64" '{data:{PANTRY_DATABASE_URL:$v}}')
ns_pantry patch secret pantry-bot-platform --type merge -p "$patch" >/dev/null

auth_patch=$(jq -cn \
  --arg h "$(printf '%s' auth-postgresql-standby.auth.svc.cluster.local | base64 -w0)" \
  --arg p "$(printf '%s' 5432 | base64 -w0)" \
  '{data:{AUTHENTIK_POSTGRESQL__HOST:$h,AUTHENTIK_POSTGRESQL__PORT:$p}}')
if ns_auth get secret auth-authentik >/dev/null 2>&1; then
  ns_auth patch secret auth-authentik --type merge -p "$auth_patch" >/dev/null
fi

for deployment in pantry-chat-worker pantry-commands-site pantry-overlay-delivery \
  pantry-private-api pantry-private-site pantry-twitch-dispatcher pantry-twitch-gateway \
  auth-authentik-server auth-authentik-worker ldap-outpost; do
  namespace=pantry-bot
  [[ "$deployment" == auth-* || "$deployment" == ldap-* ]] && namespace=auth
  k -n "$namespace" rollout restart "deployment/$deployment" >/dev/null
done
for deployment in pantry-chat-worker pantry-commands-site pantry-overlay-delivery \
  pantry-private-api pantry-private-site pantry-twitch-dispatcher pantry-twitch-gateway \
  auth-authentik-server auth-authentik-worker ldap-outpost; do
  namespace=pantry-bot
  [[ "$deployment" == auth-* || "$deployment" == ldap-* ]] && namespace=auth
  k -n "$namespace" rollout status "deployment/$deployment" --timeout=180s >/dev/null
done
echo application_ready

for url in $PANTRY_PUBLIC_URLS; do
  curl --fail --silent --show-error --max-time 15 "$url" >/dev/null || {
    echo "promotion failed: public route is not ready ($url)" >&2
    exit 1
  }
done
echo traffic_routed
