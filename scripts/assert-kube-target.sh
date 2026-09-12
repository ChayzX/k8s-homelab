#!/usr/bin/env bash
set -euo pipefail

site=${1:-}
case "$site" in
  home) expected_node=${EXPECTED_KUBE_NODE:-minecraftmachine} ;;
  oracle) expected_node=${EXPECTED_KUBE_NODE:-pantry-bot-oracle} ;;
  *) echo "usage: $0 home|oracle" >&2; exit 2 ;;
esac

kubectl_bin=${KUBECTL_BIN:-kubectl}
nodes_json=$("$kubectl_bin" get nodes -o json)
if ! jq -e --arg expected "$expected_node" '
  any(.items[]?; .metadata.name == $expected and
    any(.status.conditions[]?; .type == "Ready" and .status == "True"))
' <<<"$nodes_json" >/dev/null; then
  echo "refusing mutation: Kubernetes target is not a Ready $site cluster ($expected_node)" >&2
  exit 1
fi

echo "kube_target=$site node=$expected_node"
