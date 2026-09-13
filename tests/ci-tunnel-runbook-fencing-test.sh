#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runbook="$repo_root/docs/recovery/runbooks/ci-deployment-tunnel.md"
setup="$repo_root/ci-tunnel/MANUAL-SETUP.md"

grep -Fq 'distinct tunnel token Secret' "$runbook"
grep -Fq 'separately scoped Oracle Access service token' "$runbook"
grep -Fq 'expected site identity' "$runbook"
grep -Fq 'exactly Oracle and nowhere else' "$runbook"
grep -Fq 'ci-tunnel-token-oracle' "$setup"
grep -Fq 'independently named Oracle Cloudflare tunnel' "$setup"
grep -Fq 'do not reuse `ci-tunnel-token`' "$setup"

echo 'ci-tunnel-runbook-fencing-test=passed'
