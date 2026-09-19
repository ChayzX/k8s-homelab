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

# The witness settings are container inputs, not PodSpec fields.  A plain
# kubectl client dry-run does not reject an unknown PodSpec key, so keep this
# placement contract here: if the block is accidentally dedented, the pod can
# render while silently losing the lease configuration at admission/runtime.
assert "\n      env:\n" not in deployment, "witness env must not be a PodSpec field"
assert "\n        env:\n" in deployment, "witness env must be under the JMusicBot container"
for variable in (
    "JMUSICBOT_SITE",
    "JMUSICBOT_WITNESS_URL",
    "JMUSICBOT_WITNESS_SECRET",
    "JMUSICBOT_WITNESS_RESOURCE",
    "JMUSICBOT_LEASE_SECONDS",
):
    assert f"name: {variable}" in deployment, f"missing witness variable {variable}"

assert "type: Recreate" in deployment, "Oracle must never overlap Discord writers"
assert "path: /health" in deployment, "readiness must reflect Discord ownership/readiness"
assert "path: /live" in deployment, "liveness must remain independent of Discord readiness"
assert "if [ -f /musicbot/.jmusicbot-lease-owner ]; then" in deployment, (
    "R2 sync must be lease-marker gated"
)
assert "--exclude '.jmusicbot-lease-owner'" in deployment, (
    "R2 restore/sync must never copy the ownership marker"
)
assert deployment.count("--exclude '.jmusicbot-r2-heartbeat'") >= 2, (
    "R2 restore and sync must not copy or delete the heartbeat object"
)
assert "date -u +%Y-%m-%dT%H:%M:%SZ > /tmp/.jmusicbot-r2-heartbeat" in deployment, (
    "the owner must write a timestamped heartbeat outside the state volume"
)
assert "rclone copyto /tmp/.jmusicbot-r2-heartbeat" in deployment, (
    "the owner must publish the heartbeat after a successful state sync"
)
assert "--s3-no-check-bucket" in deployment, (
    "heartbeat upload must not require bucket-create permission"
)
PY

echo "jmusicbot-oracle-active-active-test: all assertions passed"
