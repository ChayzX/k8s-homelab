#!/usr/bin/env bash
set -euo pipefail

# A rendered Oracle standby must never start a second Discord writer merely
# because the reusable home Deployment defaults to one replica.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

kubectl kustomize "$root/docs/recovery/jmusicbot-oracle-standby" >"$rendered"

python3 - "$rendered" <<'PY'
import sys

path = sys.argv[1]
documents = [document for document in open(path, encoding="utf-8").read().split("\n---\n") if document.strip()]
deployment = next(
    document
    for document in documents
    if "kind: Deployment" in document and "name: jmusicbot" in document
)
assert "replicas: 0" in deployment, "Oracle standby must render jmusicbot with zero replicas"
PY

echo "jmusicbot-oracle-standby-test: all assertions passed"
