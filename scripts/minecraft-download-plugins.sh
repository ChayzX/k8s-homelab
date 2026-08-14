#!/usr/bin/env bash
# Download the reviewed cross-play plugins into the Minecraft image context.
# URLs and checksums are pinned deliberately; update them only after reviewing
# the upstream Geyser/Floodgate release notes and Paper compatibility.
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/chase/k8s-homelab}"
PLUGIN_DIR="${MINECRAFT_BUILD_DIR:-$ROOT_DIR/minecraft}/plugins"
mkdir -p "$PLUGIN_DIR"

download_plugin() {
    local project="$1" version="$2" build="$3" platform="$4" filename="$5" expected="$6"
    local api="https://download.geysermc.org/v2/projects/${project}/versions/${version}/builds/${build}"
    local url="${api}/downloads/${platform}"
    local tmp
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' RETURN

    curl -fsSL -o "$tmp" "$url"
    local actual
    actual="$(sha256sum "$tmp" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "checksum mismatch for ${filename}: expected ${expected}, got ${actual}" >&2
        return 1
    fi
    install -m 0444 "$tmp" "$PLUGIN_DIR/$filename"
    echo "verified ${filename} ${project} ${version} build ${build} sha256=${actual}"
}

# Geyser 2.11.1 build 1219; Floodgate 2.2.5 build 140.
download_plugin geyser 2.11.1 1219 spigot Geyser-Spigot.jar \
    855e38736d67c9ee8354758a0c4ca881b1f329c681be206189c7074667dad8df
download_plugin floodgate 2.2.5 140 spigot floodgate-spigot.jar \
    9f436c42ffd8b1091a437d7a4e16f82181b9d5314f8b1732dfa9d5a4fffb19fe
