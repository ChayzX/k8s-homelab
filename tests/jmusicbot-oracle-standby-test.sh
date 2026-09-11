#!/usr/bin/env bash
set -euo pipefail

# A rendered Oracle active-active process must stay live while the shared
# witness lease prevents a second Discord writer.
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
assert "replicas: 1" in deployment, "Oracle active-active overlay must render one live process"
PY

echo "jmusicbot-oracle-active-active-test: all assertions passed"
