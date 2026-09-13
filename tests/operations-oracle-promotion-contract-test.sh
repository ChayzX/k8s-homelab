#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runbook="$root/docs/recovery/OPERATIONS-ORACLE-PROMOTION.md"
test -s "$runbook"

python3 - "$runbook" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")

required = [
    "Name and verify the target",
    "Select and verify the restore artifact",
    "Prepare Oracle without writes",
    "Prove emergency access before the outage test",
    "Fence the home writer",
    "Enable one Oracle writer",
    "Change public routing last",
]
positions = [text.index(heading) for heading in required]
assert positions == sorted(positions), "promotion phases must remain ordered"

for phrase in (
    "scripts/assert-kube-target.sh",
    "PRAGMA",
    "integrity_check",
    "mutationMode=disabled",
    "loopback",
    "port-forward",
    "Scaling a Deployment to zero",
    "Change public routing last",
    "fence Oracle's writer domain",
    "Do not run two SQLite writers",
    "unsigned proxy header",
):
    assert phrase in text, f"missing recovery safety invariant: {phrase}"

assert text.index("Fence the home writer") < text.index("Enable one Oracle writer")
assert text.index("Enable one Oracle writer") < text.index("Change public routing last")
assert "kubectl -n operations scale deployment/operations-web --replicas=1" not in text
print("operations-oracle-promotion-contract-test: all assertions passed")
PY
