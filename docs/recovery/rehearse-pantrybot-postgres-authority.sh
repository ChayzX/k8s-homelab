#!/usr/bin/env bash
set -Eeuo pipefail

# Non-production gate for the physical-streaming candidate. This script is
# intentionally conservative: it refuses production-looking contexts and
# requires an operator-provided proof that the old writer is fenced.

: "${HOME_CONTEXT:?set HOME_CONTEXT to the disposable home kubeconfig context}"
: "${ORACLE_CONTEXT:?set ORACLE_CONTEXT to the disposable Oracle kubeconfig context}"
: "${FENCE_PROOF_FILE:?set FENCE_PROOF_FILE to a file created by the fencing adapter}"
: "${REHEARSAL_PASSWORD:?set REHEARSAL_PASSWORD to a disposable password}"

HOME_NS=pantry-bot-authority-rehearsal-home
ORACLE_NS=pantry-bot-authority-rehearsal-oracle
PRIMARY_MANIFEST="$(dirname "$0")/pantrybot-postgres-authority-primary-home.yaml"
STANDBY_MANIFEST="$(dirname "$0")/pantrybot-postgres-authority-standby-oracle.yaml"
START_NS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_FILE="${EVIDENCE_FILE:-pantrybot-postgres-authority-${START_NS}.json}"

case "$HOME_CONTEXT:$ORACLE_CONTEXT" in
  *prod*|*production*)
    echo "refusing production-looking kubeconfig context" >&2
    exit 2
    ;;
esac
[[ -s "$FENCE_PROOF_FILE" ]] || { echo "fence proof file is empty or missing" >&2; exit 2; }

validate_fence_proof() {
  python3 - "$FENCE_PROOF_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        proof = json.load(fh)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid fence proof JSON: {exc}")

required = {
    "status": "passed",
    "old_writer_write_rejected": True,
    "replication_channel_blocked": True,
}
missing = [key for key in required if key not in proof]
wrong = [key for key, value in required.items() if proof.get(key) != value]
if missing or wrong:
    raise SystemExit(
        "fence proof must contain status=passed, "
        "old_writer_write_rejected=true, and "
        "replication_channel_blocked=true "
        f"(missing={missing}, invalid={wrong})"
    )
if not isinstance(proof.get("fencing_epoch"), int) or proof["fencing_epoch"] < 1:
    raise SystemExit("fence proof must contain a positive integer fencing_epoch")
print(proof["fencing_epoch"])
PY
}

cleanup() {
  kubectl --context "$ORACLE_CONTEXT" delete namespace "$ORACLE_NS" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kubectl --context "$HOME_CONTEXT" delete namespace "$HOME_NS" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

apply_with_password() {
  local context=$1 manifest=$2 namespace=$3
  sed "s/replace-at-apply/$REHEARSAL_PASSWORD/g; s/replace-with-home-reachable-address/${HOME_PRIMARY_ADDRESS:?set HOME_PRIMARY_ADDRESS}/g" "$manifest" \
    | kubectl --context "$context" apply -f - >/dev/null
  kubectl --context "$context" -n "$namespace" wait --for=condition=ready pod -l app.kubernetes.io/name=pantry-postgres-authority --timeout=180s
}

started=$(date +%s)
apply_with_password "$HOME_CONTEXT" "$PRIMARY_MANIFEST" "$HOME_NS"
apply_with_password "$ORACLE_CONTEXT" "$STANDBY_MANIFEST" "$ORACLE_NS"

home_pod=$(kubectl --context "$HOME_CONTEXT" -n "$HOME_NS" get pod -l pantrybot.postgres/role=primary -o jsonpath='{.items[0].metadata.name}')
oracle_pod=$(kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" get pod -l pantrybot.postgres/role=standby -o jsonpath='{.items[0].metadata.name}')

kubectl --context "$HOME_CONTEXT" -n "$HOME_NS" exec "$home_pod" -- sh -ec \
  "PGPASSWORD='$REHEARSAL_PASSWORD' psql -U pantry -d pantry -v ON_ERROR_STOP=1 -c \"INSERT INTO rehearsal_events(event_id,payload) VALUES ('gate-$(date +%s)','rpo-rto-gate');\"" >/dev/null

primary_lsn=$(kubectl --context "$HOME_CONTEXT" -n "$HOME_NS" exec "$home_pod" -- sh -ec \
  "psql -U pantry -d pantry -Atc \"SELECT pg_current_wal_lsn();\"" | tr -d '\r')
replay_before=$(kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" exec "$oracle_pod" -- sh -ec \
  "PGPASSWORD='$REHEARSAL_PASSWORD' psql -U pantry -d pantry -Atc \"SELECT count(*) FROM rehearsal_events WHERE payload='rpo-rto-gate';\"" | tr -d '\r')
[[ "$replay_before" == "1" ]] || { echo "standby did not replay synthetic row" >&2; exit 1; }
replay_lsn=$(kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" exec "$oracle_pod" -- sh -ec \
  "psql -U pantry -d pantry -Atc \"SELECT pg_last_wal_replay_lsn();\"" | tr -d '\r')
wal_lag_bytes=$(kubectl --context "$HOME_CONTEXT" -n "$HOME_NS" exec "$home_pod" -- sh -ec \
  "psql -U pantry -d pantry -Atc \"SELECT COALESCE(pg_wal_lsn_diff('$primary_lsn', '$replay_lsn'), 0);\"" | tr -d '\r')

fence_started=$(date +%s)
echo "Run the external fencing adapter now. It must make the old writer reject a test write."
echo "Required proof file: $FENCE_PROOF_FILE"
fencing_epoch=$(validate_fence_proof)
fence_finished=$(date +%s)

kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" exec "$oracle_pod" -- sh -ec \
  "su postgres -c 'pg_ctl -D /var/lib/postgresql/data promote'"
kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" exec "$oracle_pod" -- sh -ec \
  "until [ \"\$(psql -U pantry -d pantry -Atc 'SELECT NOT pg_is_in_recovery();')\" = 't' ]; do sleep 1; done"
kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" label pod "$oracle_pod" pantrybot.postgres/role=primary --overwrite >/dev/null
kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" wait --for=condition=ready pod "$oracle_pod" --timeout=30s >/dev/null
oracle_endpoint=$(kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" get endpoints postgres-authority -o jsonpath='{.subsets[0].addresses[0].ip}')
[[ -n "$oracle_endpoint" ]] || { echo "promoted authority Service has no endpoint" >&2; exit 1; }
kubectl --context "$ORACLE_CONTEXT" -n "$ORACLE_NS" exec "$oracle_pod" -- sh -ec \
  "PGPASSWORD='$REHEARSAL_PASSWORD' psql -U pantry -d pantry -v ON_ERROR_STOP=1 -c \"INSERT INTO rehearsal_events(event_id,payload) VALUES ('promoted','oracle-promoted');\"" >/dev/null

finished=$(date +%s)
python3 - "$EVIDENCE_FILE" "$started" "$fence_started" "$fence_finished" "$finished" "$primary_lsn" "$replay_lsn" "$wal_lag_bytes" "$fencing_epoch" <<'PY'
import json, sys
out, started, fence_started, fence_finished, finished, primary_lsn, replay_lsn, wal_lag_bytes, fencing_epoch = sys.argv[1:]
evidence = {
    "candidate": "physical-postgres-streaming",
    "production_changed": False,
    "fence_proof_file": "operator-supplied",
    "fencing_epoch": int(fencing_epoch),
    "primary_lsn_at_boundary": primary_lsn,
    "standby_replay_lsn_at_boundary": replay_lsn,
    "wal_lag_bytes_at_boundary": float(wal_lag_bytes),
    "rto_seconds": int(finished) - int(started),
    "fence_seconds": int(fence_finished) - int(fence_started),
    "status": "passed only if the proof file demonstrates old-writer rejection",
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(evidence, fh, indent=2)
    fh.write("\n")
print(json.dumps(evidence, indent=2))
PY
