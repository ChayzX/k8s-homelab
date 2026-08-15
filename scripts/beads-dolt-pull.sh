#!/usr/bin/env bash
set -Eeuo pipefail

# Scotty reads host-local Beads databases directly; it does not pull Dolt
# changes itself. Pull all configured boards on a short host-local cadence.
PATH="/home/chase/.local/bin:/usr/local/bin:/usr/bin:/bin"
LOCK_FILE="${BEADS_PULL_LOCK_FILE:-/tmp/k8s-homelab-beads-dolt-pull.lock}"
LOG_FILE="${BEADS_PULL_LOG_FILE:-/home/chase/k8s-homelab/scripts/beads-dolt-pull.log}"

mkdir -p "$(dirname "${LOG_FILE}")"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  printf '%s skipped: pull already running\n' "$(date --iso-8601=seconds)" >>"${LOG_FILE}"
  exit 0
fi

pull_board() {
  local name="$1" checkout="$2"
  if [[ ! -d "${checkout}/.beads" ]]; then
    printf '%s %s skipped: missing %s/.beads\n' "$(date --iso-8601=seconds)" "${name}" "${checkout}" >>"${LOG_FILE}"
    return 0
  fi
  printf '%s %s starting\n' "$(date --iso-8601=seconds)" "${name}" >>"${LOG_FILE}"
  if bd -C "${checkout}" dolt pull --remote origin >>"${LOG_FILE}" 2>&1; then
    printf '%s %s success\n' "$(date --iso-8601=seconds)" "${name}" >>"${LOG_FILE}"
  else
    printf '%s %s FAILED\n' "$(date --iso-8601=seconds)" "${name}" >>"${LOG_FILE}"
    return 1
  fi
}

status=0
pull_board k8s-homelab "${BEADS_K8S_CHECKOUT:-/home/chase/k8s-homelab}" || status=1
pull_board pantry-bot "${BEADS_PANTRY_CHECKOUT:-/home/chase/Downloads/pantry-bot}" || status=1
pull_board aios "${BEADS_AIOS_CHECKOUT:-/home/chase/boards/aios}" || status=1
pull_board operations-ios-app "${BEADS_OPERATIONS_IOS_CHECKOUT:-/home/chase/boards/operations-ios-app}" || status=1
exit "${status}"
