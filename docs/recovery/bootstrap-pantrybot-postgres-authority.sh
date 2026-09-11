#!/usr/bin/env bash
set -Eeuo pipefail

# Production bootstrap gate. Default mode is read-only preflight plus a
# client-side dry-run. It never creates the Secret and never performs
# replication, promotion, or fencing.

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly MANIFEST="${SCRIPT_DIR}/pantrybot-postgres-authority-production.yaml"
readonly NAMESPACE="pantry-bot"
readonly SECRET_NAME="pantry-bot-postgres-authority"

usage() {
  cat <<'EOF'
Usage:
  KUBE_CONTEXT=<production-home-context> \
  EXTERNAL_SECRET_ESCROW_REF=<ticket-or-vault-reference> \
  ./bootstrap-pantrybot-postgres-authority.sh [--apply]

Default mode performs read-only preflight and kubectl client-side dry-run.
--apply additionally requires:
  CONFIRM_PRODUCTION_AUTHORITY_BOOTSTRAP=yes

The referenced Secret must already exist in pantry-bot and carry:
  label homelab/secret-escrow=external
  annotation homelab/secret-escrow-reference=<EXTERNAL_SECRET_ESCROW_REF>
with non-empty keys POSTGRES_USER, POSTGRES_DB, and POSTGRES_PASSWORD.
EOF
}

mode=validate
case "${1:-}" in
  "") ;;
  --apply) mode=apply ;;
  --help|-h) usage; exit 0 ;;
  *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

: "${KUBE_CONTEXT:?KUBE_CONTEXT must name the intended production home context}"
: "${EXTERNAL_SECRET_ESCROW_REF:?EXTERNAL_SECRET_ESCROW_REF must identify independently escrowed credentials}"

case "${KUBE_CONTEXT,,}" in
  *rehearsal*|*disposable*|*oracle*|*standby*)
    echo "refusing non-production-looking kubeconfig context: ${KUBE_CONTEXT}" >&2
    exit 1
    ;;
esac

if [[ "$mode" == apply && "${CONFIRM_PRODUCTION_AUTHORITY_BOOTSTRAP:-}" != yes ]]; then
  echo "refusing apply: set CONFIRM_PRODUCTION_AUTHORITY_BOOTSTRAP=yes explicitly" >&2
  exit 1
fi

kubectl --context "$KUBE_CONTEXT" get namespace "$NAMESPACE" >/dev/null
kubectl --context "$KUBE_CONTEXT" get storageclass local-path >/dev/null

secret_json="$(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get secret "$SECRET_NAME" -o json)"
SECRET_JSON="$secret_json" ESCROW_REF="$EXTERNAL_SECRET_ESCROW_REF" python3 - <<'PY'
import json
import os
import base64

secret = json.loads(os.environ["SECRET_JSON"])
metadata = secret.get("metadata", {})
labels = metadata.get("labels", {})
annotations = metadata.get("annotations", {})
if labels.get("homelab/secret-escrow") != "external":
    raise SystemExit("Secret is not marked as externally escrowed")
if annotations.get("homelab/secret-escrow-reference") != os.environ["ESCROW_REF"]:
    raise SystemExit("Secret escrow annotation does not match EXTERNAL_SECRET_ESCROW_REF")

keys = set((secret.get("data") or {}).keys())
required = {"POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD"}
missing = sorted(required - keys)
if missing:
    raise SystemExit("Secret is missing required keys: " + ", ".join(missing))
empty = sorted(
    key for key in required
    if not base64.b64decode(secret["data"][key], validate=True)
)
if empty:
    raise SystemExit("Secret has empty required keys: " + ", ".join(empty))
print("external escrow metadata and required Secret keys verified")
PY

kubectl --context "$KUBE_CONTEXT" apply --dry-run=client -f "$MANIFEST" >/dev/null
echo "client-side dry-run passed for ${MANIFEST}"

if [[ "$mode" == apply ]]; then
  echo "Applying production PostgreSQL authority manifest to context ${KUBE_CONTEXT}"
  kubectl --context "$KUBE_CONTEXT" apply -f "$MANIFEST"
  echo "Apply complete; replication/fencing and PantryBot application cutover remain separate gates."
fi
