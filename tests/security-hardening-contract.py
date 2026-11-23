#!/usr/bin/env python3
"""Static contract for the first #456 Kubernetes security hardening batch."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TUNNELS = (
    "ci-tunnel/20-deployment.yaml",
    "observability/mcp-grafana-cloudflared.yaml",
    "observability/windows-alloy-metrics-cloudflared.yaml",
    "pantry-bot/62-deployment-commands-cloudflared.yaml",
    "pantry-bot/66-deployment-app-cloudflared.yaml",
)

for relative_path in TUNNELS:
    manifest = (ROOT / relative_path).read_text()
    # Isolate only the 'cloudflared' container, not sibling containers.
    match = re.search(
        r"(?ms)^        - name: cloudflared\n"
        r"(?P<body>.*?)(?=^        - name: |\Z)",
        manifest,
    )
    assert match, f"{relative_path}: no cloudflared container"
    body = match.group("body")
    for required in (
        "readOnlyRootFilesystem: true",
        "allowPrivilegeEscalation: false",
        "drop: [\"ALL\"]",
    ):
        assert required in body, f"{relative_path}: missing {required}"
    assert "automountServiceAccountToken: false" in manifest, relative_path

ksm = (ROOT / "observability/kube-state-metrics.yaml").read_text()
assert not re.search(r"(?m)^\s+- secrets\s*$", ksm), (
    "kube-state-metrics must not have access to Kubernetes Secrets"
)
assert 'verbs: ["list", "watch"]' in ksm, "read-only metrics permissions removed"
print("security-hardening-contract=passed")
