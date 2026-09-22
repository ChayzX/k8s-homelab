#!/usr/bin/env bash
set -Eeuo pipefail

# Shared PantryBot writer-domain fence for one k3s site (#191).
#
# Fences EVERY listed PostgreSQL StatefulSet plus the DB-writing app
# Deployments and the site's app tunnel connector, then positively verifies
# all of it. Callers are thin wrappers that set the site's lists:
#   fence-oracle-postgres-local.sh   (Oracle, local admin kubectl)
#   fence-home-direct-from-oracle.sh (Home, least-privilege kubeconfig)
#
# Proof rules:
# - A StatefulSet is fenced only when spec.replicas==0 AND its "-0" pod object
#   is gone. The pod is deleted gracefully (short grace, never --force), so
#   the API object disappears only after the kubelet confirms the container
#   stopped. A pod on a NotReady node never disappears, so it times out and
#   FAILS instead of passing on a forced API deletion that proves nothing.
# - A Deployment is fenced only when spec.replicas==0 AND status.replicas==0.
# - A resource that does not exist counts as fenced and is named in the
#   output. Any other lookup error (Forbidden, Unauthorized, ...) is a hard
#   failure: an authorization problem must never look like a missing object.
# - Only the first API probe may classify the site as unreachable (exit 75),
#   and only when FENCE_ALLOW_UNREACHABLE=1 (remote callers). Everything
#   after that probe, and refused/auth errors at the probe, are hard failures.
#
# Env (set by the wrapper):
#   FENCE_SCOPE              label for output, e.g. oracle-local
#   FENCE_KUBECTL            kubectl binary
#   FENCE_KUBECONFIG         optional kubeconfig path
#   FENCE_NAMESPACE          namespace (default pantry-bot)
#   FENCE_STATEFULSETS       space-separated StatefulSet names (required)
#   FENCE_DEPLOYMENTS        space-separated Deployment names (may be empty)
#   FENCE_TIMEOUT_SECONDS    total fence budget, start to verdict (default 45)
#   FENCE_POD_GRACE_SECONDS  pod termination grace (default 5)
#   FENCE_ALLOW_UNREACHABLE  1 = network silence at first probe exits 75

SCOPE="${FENCE_SCOPE:?FENCE_SCOPE required}"
KUBECTL_BIN="${FENCE_KUBECTL:-kubectl}"
NS="${FENCE_NAMESPACE:-pantry-bot}"
TIMEOUT_SECONDS="${FENCE_TIMEOUT_SECONDS:-45}"
GRACE_SECONDS="${FENCE_POD_GRACE_SECONDS:-5}"
ALLOW_UNREACHABLE="${FENCE_ALLOW_UNREACHABLE:-0}"
read -r -a STATEFULSETS <<<"${FENCE_STATEFULSETS:-}"
read -r -a DEPLOYMENTS <<<"${FENCE_DEPLOYMENTS:-}"

# Never fenced by PantryBot automation: the shared tunnel 59569621 connector
# (SSH/RDP/k8s-api/Authentik/oauth) and the active-active, DB-less commands
# path. A list naming one is a configuration error, not something to skip.
PROTECTED_DEPLOYMENTS=(cloudflared commands-cloudflared pantry-commands-site)

fail() {
  echo "fence_status=failed scope=$SCOPE reason=$1" >&2
  exit 1
}

k() {
  if [[ -n "${FENCE_KUBECONFIG:-}" ]]; then
    "$KUBECTL_BIN" --kubeconfig="$FENCE_KUBECONFIG" --request-timeout=10s -n "$NS" "$@"
  else
    "$KUBECTL_BIN" --request-timeout=10s -n "$NS" "$@"
  fi
}

oneline() { tr '\n' ' ' <<<"$1"; }

MODE="${1:-}"
[[ ( "$MODE" == "--confirm" || "$MODE" == "--dry-run" ) && "$#" == 1 ]] || fail "explicit_confirmation_required"
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_timeout"
[[ "$GRACE_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "invalid_grace"
(( ${#STATEFULSETS[@]} > 0 )) || fail "no_statefulsets_configured"
[[ -z "${FENCE_KUBECONFIG:-}" || -r "$FENCE_KUBECONFIG" ]] || fail "kubeconfig_unreadable"
for d in "${DEPLOYMENTS[@]}"; do
  for p in "${PROTECTED_DEPLOYMENTS[@]}"; do
    [[ "$d" != "$p" ]] || fail "protected_deployment_in_fence_list:$d"
  done
done

# The budget covers the WHOLE fence (probe, scale calls and verification),
# so callers can set their hook timeout above it with a known margin.
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))

if ! probe_err="$(k version 2>&1 >/dev/null)"; then
  if [[ "$ALLOW_UNREACHABLE" == 1 ]] \
    && grep -qiE 'i/o timeout|no route to host|network is unreachable|context deadline exceeded|Client\.Timeout' <<<"$probe_err" \
    && ! grep -qi 'connection refused' <<<"$probe_err"; then
    echo "writer_fence=unreachable scope=$SCOPE $(oneline "$probe_err")" >&2
    exit 75
  fi
  fail "kubernetes_api_unavailable:$(oneline "$probe_err")"
fi

# lookup KIND NAME JSONPATH: sets VALUE; returns 3 when NotFound. Runs in
# the main shell (never inside $(...)) so a hard failure really exits.
VALUE=""
lookup() {
  local out
  if out="$(k get "$1" "$2" -o "jsonpath=$3" 2>&1)"; then
    VALUE="$out"
    return 0
  fi
  grep -q '(NotFound)' <<<"$out" && { VALUE=""; return 3; }
  fail "lookup_failed:$1/$2:$(oneline "$out")"
}

present_sts=() present_deploy=() absent=()

# --dry-run: probe + every lookup (proves reachability and RBAC), no writes.
if [[ "$MODE" == "--dry-run" ]]; then
  for s in "${STATEFULSETS[@]}"; do
    rc=0; lookup statefulset "$s" '{.metadata.name}' || rc=$?
    (( rc == 3 )) && absent+=("statefulset/$s") || present_sts+=("$s")
  done
  for d in "${DEPLOYMENTS[@]}"; do
    rc=0; lookup deployment "$d" '{.metadata.name}' || rc=$?
    (( rc == 3 )) && absent+=("deployment/$d") || present_deploy+=("$d")
  done
  join() { local IFS=,; echo "$*"; }
  echo "fence_status=dry_run_ok scope=$SCOPE statefulsets=$(join "${present_sts[@]}") deployments=$(join "${present_deploy[@]}") absent=$(join "${absent[@]}")"
  exit 0
fi

# Phase 1: issue every scale-down first (DB first: the hard guarantee lands
# soonest; writers still up merely fail to connect), then the pod deletes.
for s in "${STATEFULSETS[@]}"; do
  rc=0; lookup statefulset "$s" '{.metadata.name}' || rc=$?
  if (( rc == 3 )); then absent+=("statefulset/$s"); continue; fi
  out="$(k patch statefulset "$s" --type=merge -p '{"spec":{"replicas":0}}' 2>&1)" \
    || fail "scale_failed:statefulset/$s:$(oneline "$out")"
  present_sts+=("$s")
done
for d in "${DEPLOYMENTS[@]}"; do
  rc=0; lookup deployment "$d" '{.metadata.name}' || rc=$?
  if (( rc == 3 )); then absent+=("deployment/$d"); continue; fi
  out="$(k patch deployment "$d" --type=merge -p '{"spec":{"replicas":0}}' 2>&1)" \
    || fail "scale_failed:deployment/$d:$(oneline "$out")"
  present_deploy+=("$d")
done
for s in "${present_sts[@]}"; do
  pod="$s-0"
  out="$(k delete pod "$pod" --grace-period="$GRACE_SECONDS" --wait=false 2>&1)" && continue
  grep -q '(NotFound)' <<<"$out" || fail "pod_delete_failed:$pod:$(oneline "$out")"
done

# Phase 2: one bounded verification loop over everything, within the budget.
# Items proven fenced are dropped from later passes (the lease holder is the
# only one allowed to scale them back up, and it is this caller).
todo_sts=("${present_sts[@]}") todo_deploy=("${present_deploy[@]}")
while :; do
  pending=() next_sts=() next_deploy=()
  for s in "${todo_sts[@]}"; do
    rc=0; lookup statefulset "$s" '{.spec.replicas}' || rc=$?
    ok=1
    (( rc == 3 )) || [[ "$VALUE" == 0 ]] || { ok=0; pending+=("statefulset/$s:replicas=$VALUE"); }
    rc=0; lookup pod "$s-0" '{.spec.nodeName}' || rc=$?
    (( rc == 3 )) || { ok=0; pending+=("pod/$s-0:node=${VALUE:-unscheduled}"); }
    (( ok )) || next_sts+=("$s")
  done
  for d in "${todo_deploy[@]}"; do
    rc=0; lookup deployment "$d" '{.spec.replicas}/{.status.replicas}' || rc=$?
    (( rc == 3 )) || [[ "$VALUE" == "0/" || "$VALUE" == "0/0" ]] || { pending+=("deployment/$d:$VALUE"); next_deploy+=("$d"); }
  done
  todo_sts=("${next_sts[@]}") todo_deploy=("${next_deploy[@]}")
  (( ${#pending[@]} == 0 )) && break
  (( $(date +%s) < deadline )) || fail "not_fenced_within_${TIMEOUT_SECONDS}s:${pending[*]}"
  sleep 1
done

join() { local IFS=,; echo "$*"; }
echo "fence_status=passed scope=$SCOPE statefulsets=$(join "${present_sts[@]}") deployments=$(join "${present_deploy[@]}") absent=$(join "${absent[@]}")"
