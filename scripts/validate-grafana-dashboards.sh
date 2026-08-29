#!/usr/bin/env bash
# GitHub issue: k8s-homelab-ytt
# Validate Grafana's editable JSON sources and the generated ConfigMap copy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="$REPO_ROOT/dashboards"
CONFIGMAP="$DASHBOARD_DIR/dashboards-configmap.yaml"

for required in jq python3; do
  command -v "$required" >/dev/null || {
    echo "missing required command: $required" >&2
    exit 1
  }
done

mapfile -t dashboard_files < <(find "$DASHBOARD_DIR" -maxdepth 1 -type f -name '*.json' -print | sort)
(( ${#dashboard_files[@]} > 0 )) || {
  echo "no dashboard JSON files found" >&2
  exit 1
}

declare -A dashboard_uids=()
for dashboard_file in "${dashboard_files[@]}"; do
  jq -e '
    (.uid | type == "string" and length > 0) and
    (.title | type == "string" and length > 0) and
    (.panels | type == "array") and
    ([.panels[]?.datasource?.uid, .panels[]?.targets[]?.datasource?.uid]
      | map(select(. != null))
      | all(type == "string" and length > 0))
  ' "$dashboard_file" >/dev/null

  uid="$(jq -r '.uid' "$dashboard_file")"
  if [[ -n "${dashboard_uids[$uid]:-}" ]]; then
    echo "duplicate dashboard uid '$uid': ${dashboard_uids[$uid]} and $dashboard_file" >&2
    exit 1
  fi
  dashboard_uids[$uid]="$dashboard_file"

  duplicate_panel_ids="$(jq -r '[.panels[]?.id | select(. != null)] | group_by(.)[] | select(length > 1) | .[0]' "$dashboard_file")"
  if [[ -n "$duplicate_panel_ids" ]]; then
    echo "duplicate panel id(s) in $dashboard_file: $duplicate_panel_ids" >&2
    exit 1
  fi
done

# Parse the generated ConfigMap locally. This validator runs before the
# deployment job establishes its Kubernetes API tunnel, so kubectl discovery
# cannot be part of validation.
python3 - "$CONFIGMAP" "$DASHBOARD_DIR" <<'PY'
import pathlib
import re
import sys

configmap = pathlib.Path(sys.argv[1]).read_text()
dashboard_dir = pathlib.Path(sys.argv[2])
matches = list(re.finditer(r"^  ([^\s]+\.json): \|\n", configmap, re.MULTILINE))
embedded = {}
for index, match in enumerate(matches):
    end = matches[index + 1].start() if index + 1 < len(matches) else len(configmap)
    body = configmap[match.end():end]
    body_lines = []
    for line in body.splitlines():
        if line and not line.startswith("    "):
            break
        body_lines.append(line)
    embedded[match.group(1)] = "\n".join(
        line[4:] if line.startswith("    ") else line
        for line in body_lines
    ).rstrip("\n")

sources = {
    path.name: path.read_text().rstrip("\n")
    for path in sorted(dashboard_dir.glob("*.json"))
}
if sorted(embedded) != sorted(sources):
    print("dashboard ConfigMap keys do not match dashboard JSON sources", file=sys.stderr)
    print("sources:", sorted(sources), file=sys.stderr)
    print("embedded:", sorted(embedded), file=sys.stderr)
    raise SystemExit(1)
for key, source in sources.items():
    if embedded[key] != source:
        print(f"{key} differs from its embedded ConfigMap copy; regenerate dashboards-configmap.yaml", file=sys.stderr)
        raise SystemExit(1)
PY

configmap_bytes="$(wc -c < "$CONFIGMAP")"
if (( configmap_bytes >= 900000 )); then
  echo "dashboard ConfigMap is ${configmap_bytes} bytes; split it before approaching the 1 MiB limit" >&2
  exit 1
fi

echo "Validated ${#dashboard_files[@]} dashboards; ConfigMap copy is synchronized (${configmap_bytes} bytes)."
