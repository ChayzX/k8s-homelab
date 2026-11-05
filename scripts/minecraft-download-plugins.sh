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
    6c090fac496d8a8b9f51040eb39dc350a5f4e5384e9c76bc0efdd185ba1c9e67
download_plugin floodgate 2.2.5 140 spigot floodgate-spigot.jar \
    21570aff9ce17d6983928e8552777760e1ede5050026b04c686b0ae112e6fd7e
