#!/usr/bin/env bash
set -Eeuo pipefail

script=${1:-scripts/k3s-control-plane-isolated-restore.sh}

help_output=$(bash "$script" --help)
grep -q -- '--artifact PATH' <<<"$help_output"
grep -q -- '--timeout SECONDS' <<<"$help_output"
grep -q -- 'Non-production' <<<"$help_output"
if grep -q -- 'docker network create --internal' "$script"; then
  echo 'restore network must retain a default route for k3s startup' >&2
  exit 1
fi

if bash "$script" --unknown-option >/tmp/k3s-isolated-restore-test.err 2>&1; then
  echo 'unknown option unexpectedly succeeded' >&2
  exit 1
fi

echo 'k3s-isolated-restore-test=passed'
