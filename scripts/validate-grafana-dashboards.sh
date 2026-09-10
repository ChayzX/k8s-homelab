#!/usr/bin/env bash
# Validate Grafana's editable JSON sources and the generated ConfigMap copy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="$REPO_ROOT/dashboards"
CONFIGMAP="$DASHBOARD_DIR/dashboards-configmap.yaml"

for required in jq kubectl; do
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

configmap_json="$(kubectl create --dry-run=client -f "$CONFIGMAP" -o json)"
mapfile -t embedded_keys < <(jq -r '.data | keys[]' <<<"$configmap_json" | sort)
mapfile -t source_keys < <(printf '%s\n' "${dashboard_files[@]##*/}" | sort)

if [[ "${embedded_keys[*]}" != "${source_keys[*]}" ]]; then
  echo "dashboard ConfigMap keys do not match dashboard JSON sources" >&2
  diff <(printf '%s\n' "${source_keys[@]}") <(printf '%s\n' "${embedded_keys[@]}") || true
  exit 1
fi

for dashboard_file in "${dashboard_files[@]}"; do
  key="${dashboard_file##*/}"
  embedded="$(jq -r --arg key "$key" '.data[$key]' <<<"$configmap_json")"
  source="$(sed -e '${/^$/d;}' "$dashboard_file")"
  if [[ "$embedded" != "$source" ]]; then
    echo "$key differs from its embedded ConfigMap copy; regenerate dashboards-configmap.yaml" >&2
    exit 1
  fi
done

configmap_bytes="$(wc -c < "$CONFIGMAP")"
if (( configmap_bytes >= 900000 )); then
  echo "dashboard ConfigMap is ${configmap_bytes} bytes; split it before approaching the 1 MiB limit" >&2
  exit 1
fi

echo "Validated ${#dashboard_files[@]} dashboards; ConfigMap copy is synchronized (${configmap_bytes} bytes)."
