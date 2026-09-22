#!/usr/bin/env bash
set -Eeuo pipefail

# Scoped fence of the Home PantryBot writer domain, invoked from Oracle (and
# later Canada) directly against minecraftmachine's k3s API with a
# least-privilege kubeconfig (pantry-postgres-fencer-rbac.yaml). Fences every
# Home PantryBot PostgreSQL StatefulSet - not just the last primary, since a
# stale replicas=1 StatefulSet can resurrect an old primary - plus the
# DB-writing apps and the PantryBot-App tunnel connector.
#
# Only the first API probe may classify Home as unreachable (exit 75, accepted
# by the composite as fenced-by-lease-expiry), and only on network silence:
# "connection refused" means the host is up with its API down, and k3s's
# containers keep running without the API, so a writer may be alive - that
# stays a hard failure, as does any failure after the probe.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Every name here must be granted in pantry-postgres-fencer-rbac.yaml
# (test_pantry_postgres_fencer_rbac.py enforces it).
HOME_PANTRY_STATEFULSETS="postgres-authority-standby-home-canada postgres-authority-home-v2 postgres-authority-home-failback postgres-authority-home postgres-authority-home-return postgres-authority"
PANTRY_WRITER_DEPLOYMENTS="pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared pantry-bot"

statefulsets="${PANTRY_HOME_STATEFULSETS:-$HOME_PANTRY_STATEFULSETS}"
# Legacy single-name override from postgres-fence.env: always included.
if [[ -n "${PANTRY_HOME_STATEFULSET:-}" && " $statefulsets " != *" $PANTRY_HOME_STATEFULSET "* ]]; then
  statefulsets="$PANTRY_HOME_STATEFULSET $statefulsets"
fi

FENCE_SCOPE=home-direct \
FENCE_KUBECTL="${HOME_KUBECTL:-kubectl}" \
FENCE_KUBECONFIG="${HOME_FENCER_KUBECONFIG:-/etc/failover-witness/pantry-postgres-fencer-home.kubeconfig}" \
FENCE_NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}" \
FENCE_STATEFULSETS="$statefulsets" \
FENCE_DEPLOYMENTS="${PANTRY_HOME_FENCE_DEPLOYMENTS-$PANTRY_WRITER_DEPLOYMENTS}" \
FENCE_TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}" \
FENCE_ALLOW_UNREACHABLE=1 \
  exec "$SCRIPT_DIR/pantry-writer-fence.sh" "$@"
