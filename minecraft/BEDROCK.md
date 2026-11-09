# Bedrock cross-play

The server image includes the checksum-pinned Geyser-Spigot and Floodgate
plugins. Geyser bridges Bedrock protocol traffic to Paper; Floodgate lets
Bedrock players authenticate without owning a Java Edition account.

Current image inputs:

- Geyser 2.12.1 build 1295
- Floodgate 2.2.5 build 141
- NetherNet signaling and gameplay: TCP and UDP `19132`
- LAN endpoint: `192.168.40.208:19132`

For players outside the LAN, forward **TCP and UDP 19132** on the router to the
Minecraft host. TCP carries signaling and UDP carries the WebRTC data channels.
The Java port forward remains TCP 25565.

MinecraftMachine's Flannel pod MTU is 1200 bytes. The image applies
`geyser-nethernet-mtu.patch` to the pinned Geyser build so built-in NetherNet
peers use that MTU. The usual `advanced.bedrock.mtu` setting controls RakNet.
When updating Geyser, rebase the patch and update the pinned source and jar
checksums in the Dockerfile, or remove the patch if upstream provides a
NetherNet MTU setting.

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
