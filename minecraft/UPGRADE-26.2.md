# Paper 26.2 migration preparation

This records the completed, controlled migration from Paper 1.21.11 to Paper
26.2. The live deployment now runs the verified
`localhost/paper-minecraft:26.2-b112` image.

## Evidence checked

On 2026-08-14, PaperMC's Fill API returned a stable Paper `26.2` build:

```text
GET https://fill.papermc.io/v3/projects/paper/versions/26.2/builds
build: 112
jar: paper-26.2-112.jar
sha256: bd3a58cf96874e5ea6643f5f6fe9b4f5bf9e34b795fa078c2f0ee8b98b2f907e
```

The Paper version metadata identifies Java 25 as the minimum runtime for the
26.2 line. The current image uses Eclipse Temurin 25. `Dockerfile` accepts
`JAVA_VERSION` and `MC_VERSION` build args, with Java 25 / Minecraft 26.2 as
the current defaults; the prior 1.21.11 image remains available for rollback.

## Required review before any production change

1. Read the official Minecraft 26.2 release notes and Paper 26.2 notes.
2. Inventory every installed plugin and confirm a build compatible with 26.2
   and Java 25. Do not start the live world with unverified plugins.
3. Review `server.properties`, `bukkit.yml`, `spigot.yml`, Paper config, and
   datapacks for renamed or removed settings and data-format changes.
4. Take a fresh, independently restorable world backup immediately before
   the cutover. Verify the archive and record its location.
5. Build and smoke-test off the live world data:

   ```bash
   cd /home/chase/k8s-homelab/minecraft
   curl -fsSL -o paper.jar \
     https://fill-data.papermc.io/v1/objects/bd3a58cf96874e5ea6643f5f6fe9b4f5bf9e34b795fa078c2f0ee8b98b2f907e/paper-26.2-112.jar
   echo 'bd3a58cf96874e5ea6643f5f6fe9b4f5bf9e34b795fa078c2f0ee8b98b2f907e  paper.jar' | sha256sum -c -
   docker build --build-arg JAVA_VERSION=25 --build-arg MC_VERSION=26.2 \
     -t localhost/paper-minecraft:26.2-b112 .
   docker run --rm --entrypoint sh localhost/paper-minecraft:26.2-b112 \
     -c 'java -version; id; test -r /opt/minecraft/paper.jar'
   ```

   A real server smoke test must use a disposable copy of the world, never
   the production PVC or `/home/chase/minecraft`.
6. Only after the above passes: side-load the image, update the manifest tag,
   apply the existing `Recreate` rollout, and verify readiness plus RCON
   version/list response. Follow [MIGRATION.md](MIGRATION.md) for shutdown,
   backup, rollback, and port checks.

## Auto-update safety

`scripts/minecraft-auto-update.sh` is now pinned to the reviewed 26.2 line and
passes Java 25 / Minecraft 26.2 build args explicitly. It still never changes
the major version automatically; a future major upgrade requires the same
human changelog, backup, compatibility, and rollback review.
