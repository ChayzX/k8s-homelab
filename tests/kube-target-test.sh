#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
chmod +x "$ROOT/tests/fixtures/kubectl-target-ready" "$ROOT/tests/fixtures/kubectl-target-wrong"

if KUBECTL_BIN="$ROOT/tests/fixtures/kubectl-target-ready" \
  "$ROOT/scripts/assert-kube-target.sh" home; then :; else
  echo "ready home target should pass" >&2; exit 1
fi

if KUBECTL_BIN="$ROOT/tests/fixtures/kubectl-target-wrong" \
  "$ROOT/scripts/assert-kube-target.sh" home; then
  echo "wrong target should fail" >&2; exit 1
fi

if "$ROOT/scripts/assert-kube-target.sh" invalid-site >/dev/null 2>&1; then
  echo "invalid site should fail" >&2; exit 1
fi

echo "kube-target-test=passed"
