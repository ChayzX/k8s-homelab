#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

kubectl kustomize "$root/docs/recovery/ci-tunnel-oracle-standby" >"$rendered"

python3 - "$rendered" <<'PY'
import sys

documents = [doc for doc in open(sys.argv[1], encoding="utf-8").read().split("\n---\n") if doc.strip()]
deployment = next(doc for doc in documents if "kind: Deployment" in doc and "name: cloudflared" in doc)
assert "namespace: ci-tunnel" in deployment
assert "replicas: 0" in deployment, "Oracle CI tunnel must remain disabled until target credentials exist"
assert "name: ci-tunnel-token" in deployment
assert "cluster-admin" not in "\n".join(documents).lower()
print("ci-tunnel-oracle-standby-test: all assertions passed")
PY
