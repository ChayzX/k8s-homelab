# Bedrock cross-play

The server image includes the checksum-pinned Geyser-Spigot and Floodgate
plugins. Geyser bridges Bedrock protocol traffic to Paper; Floodgate lets
Bedrock players authenticate without owning a Java Edition account.

Current image inputs:

- Geyser 2.11.1 build 1219
- Floodgate 2.2.5 build 140
- Bedrock listener: UDP `19132`
- LAN endpoint: `192.168.40.208:19132`

For players outside the LAN, forward **UDP 19132** on the router to the
Minecraft host. Cloudflare's HTTP tunnel does not carry this Bedrock UDP
traffic. The Java port forward remains TCP 25565.

The generated Floodgate `key.pem` and expanded Geyser config stay on the
Minecraft PVC and are included in the normal world/PVC backup. Never commit or
share `key.pem`; it is an authentication secret.

The official setup guidance is at:

- <https://geysermc.org/wiki/geyser/setup/self/paper-spigot/>
- <https://geysermc.org/wiki/floodgate/setup/paper-spigot/>

To refresh the pinned plugin artifacts after a reviewed upstream release:

```bash
bash scripts/minecraft-download-plugins.sh
```

That script verifies SHA-256 checksums before the jars enter a new image.
