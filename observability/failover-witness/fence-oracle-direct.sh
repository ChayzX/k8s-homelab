#!/usr/bin/env bash
set -Eeuo pipefail

# Scoped remote fence of Oracle's PantryBot writer domain, run from another
# site's promoter (Home today) against Oracle's k3s API with the
# least-privilege kubeconfig from pantry-postgres-fencer-oracle-rbac.yaml.
# Lists must match fence-oracle-postgres-local.sh (test enforced). Only
# first-probe network silence may exit 75 (fenced by lease expiry).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

ORACLE_PANTRY_STATEFULSETS="postgres-authority-standby-oracle-v2 postgres-authority-standby-oracle postgres-authority-standby postgres-authority-standby-home postgres-authority-standby-home-v2 postgres-authority-standby-reseed postgres-authority-standby-reseed-v2"
PANTRY_WRITER_DEPLOYMENTS="pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared"

FENCE_SCOPE=oracle-direct \
FENCE_KUBECTL="${ORACLE_KUBECTL:-kubectl}" \
FENCE_KUBECONFIG="${ORACLE_FENCER_KUBECONFIG:-/etc/failover-witness/pantry-postgres-fencer-oracle.kubeconfig}" \
FENCE_NAMESPACE="${PANTRY_NAMESPACE:-pantry-bot}" \
FENCE_STATEFULSETS="$ORACLE_PANTRY_STATEFULSETS" \
FENCE_DEPLOYMENTS="$PANTRY_WRITER_DEPLOYMENTS" \
FENCE_TIMEOUT_SECONDS="${PANTRY_POSTGRES_FENCE_TIMEOUT_SECONDS:-45}" \
FENCE_ALLOW_UNREACHABLE=1 \
  exec "$SCRIPT_DIR/pantry-writer-fence.sh" "$@"
