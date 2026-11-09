#!/usr/bin/env bash
# Download the reviewed cross-play plugins into the Minecraft image context.
# URLs and checksums are pinned deliberately; update them only after reviewing
# the upstream Geyser/Floodgate release notes and Paper compatibility.
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
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

# Keep the Geyser pin in sync with the NetherNet patch in minecraft/Dockerfile.
download_plugin geyser 2.12.1 1295 spigot Geyser-Spigot.jar \
    b60ff8f9a007f30823cf40173532a0390e6150cdfc577551f6f37bdd5ccd1528
download_plugin floodgate 2.2.5 141 spigot floodgate-spigot.jar \
    21570aff9ce17d6983928e8552777760e1ede5050026b04c686b0ae112e6fd7e
