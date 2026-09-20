#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
workflow="$root/.github/workflows/jmusicbot-deploy.yml"
guard="$root/scripts/assert-jmusicbot-home-owner.sh"

test -x "$guard"
grep -Fq 'scripts/assert-jmusicbot-home-owner.sh' "$workflow"
set_image=$(grep -n 'kubectl set image deployment/jmusicbot' "$workflow" | cut -d: -f1)
restart=$(grep -n 'kubectl rollout restart deployment/jmusicbot' "$workflow" | cut -d: -f1)
guard_lines=$(grep -n 'scripts/assert-jmusicbot-home-owner.sh' "$workflow" | cut -d: -f1)
test "$(awk -v n="$set_image" '$1<n {c++} END{print c+0}' <<<"$guard_lines")" -ge 1
test "$(awk -v n="$restart" '$1<n {c++} END{print c+0}' <<<"$guard_lines")" -ge 2

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat >"$tmp/kubectl" <<'EOF'
#!/usr/bin/env bash
cat <<'JSON'
{"spec":{"replicas":1},"status":{"readyReplicas":1,"availableReplicas":1}}
JSON
EOF
chmod +x "$tmp/kubectl"
KUBECTL_BIN="$tmp/kubectl" "$guard" >/dev/null
sed -i 's/"readyReplicas":1/"readyReplicas":0/' "$tmp/kubectl"
set +e
KUBECTL_BIN="$tmp/kubectl" "$guard" >/dev/null 2>&1
rc=$?
set -e
test "$rc" -eq 3
echo 'jmusicbot-home-owner-guard-test=passed'
