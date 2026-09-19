# syntax=docker/dockerfile:1
#
# Paper server image — a container around the *existing* bare-metal setup.
#
# DESIGN NOTES (read before changing anything)
# ============================================================================
# * This is deliberately NOT itzg/minecraft-server. The bare-metal server is
#   started today by msh as:
#       java -Xms4G -Xmx12G -jar paper.jar --nogui   (cwd /home/chase/minecraft)
#   The goal is the smallest possible delta from that, so the only behavioural
#   changes are the ones the migration plan explicitly calls for. No auto-
#   downloading, no plugin manager, no config templating, no entrypoint that
#   rewrites server.properties.
#
# * JAVA VERSION: Java 25. Verified, not assumed — Paper 26.2 requires Java 25.
#   Keep the version in the build args and image label so a future major
#   upgrade remains an explicit, reviewable change.
#
# * GLIBC, NOT MUSL: server.properties has use-native-transport=true. Paper
#   ships netty's epoll natives built against glibc. On an Alpine/musl base the
#   JVM silently falls back to NIO and you lose native transport — a real
#   performance regression that is easy to miss. Hence eclipse-temurin *-noble.
#
# * The server jar is baked into the image at /opt/minecraft/paper.jar and the
#   PVC holds only mutable state. That makes a Paper upgrade an image change
#   (auditable, rollback-able) instead of a file drop on a volume.
#
# BUILD
# ============================================================================
#   The build context must contain the server jar and verified cross-play
#   plugin jars. Prepare them with:
#
#     bash ../scripts/minecraft-download-plugins.sh
#
#   Copy the *running* Paper jar so the container runs byte-identical bits to
#   what is live today:
#
#     cp /home/chase/minecraft/paper.jar /home/chase/k8s-homelab/minecraft/paper.jar
#     cd /home/chase/k8s-homelab/minecraft
#     docker build -t localhost/paper-minecraft:26.2-b112 .
#
#   (paper.jar is .gitignore'd — 55 MB does not belong in the repo.)
#   Current installed build: Paper 26.2-112.
#   Keep the image tag in sync with the build number so `kubectl describe pod`
#   tells you exactly which Paper build is running.


# ---------------------------------------------------------------------------
# Stage 1 — compile the tiny RCON client. Needs javac, which the JRE lacks.
# Nothing from this stage but a ~4 KB jar reaches the runtime image.
# ---------------------------------------------------------------------------
ARG JAVA_VERSION=25
ARG MC_VERSION=26.2

FROM eclipse-temurin:${JAVA_VERSION}-jdk-noble AS rcon-build

WORKDIR /build
COPY Rcon.java ./
RUN set -eux; \
    javac -Xlint:all -d classes Rcon.java; \
    jar --create --file rcon.jar --main-class Rcon -C classes .


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM eclipse-temurin:${JAVA_VERSION}-jre-noble

ARG JAVA_VERSION=25
ARG MC_VERSION=26.2

LABEL org.opencontainers.image.title="paper-minecraft" \
      org.opencontainers.image.description="Paper ${MC_VERSION} Minecraft server, data dir mounted at /data" \
      org.opencontainers.image.source="https://github.com/PaperMC/Paper" \
      homelab.chase/minecraft-version="${MC_VERSION}" \
      homelab.chase/java-version="${JAVA_VERSION}"

# Every file under /home/chase/minecraft is owned 1000:1000 and the PVC keeps
# that ownership, so the container must run as exactly 1000:1000.
#
# The Ubuntu Noble base already ships a user named `ubuntu` at uid/gid 1000.
# Reclaim the id rather than fighting it, so the account name in logs, `id`,
# and $HOME all make sense. This is defensive on purpose: it works whether or
# not a future base image tag happens to occupy uid 1000.
RUN set -eux; \
    if getent passwd 1000 >/dev/null; then userdel -f "$(getent passwd 1000 | cut -d: -f1)"; fi; \
    if getent group 1000 >/dev/null; then groupdel "$(getent group 1000 | cut -d: -f1)" || true; fi; \
    groupadd --gid 1000 minecraft; \
    useradd --uid 1000 --gid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin minecraft; \
    mkdir -p /data /opt/minecraft; \
    chown 1000:1000 /data

# The server jar. Read-only at runtime; root-owned by design.
COPY paper.jar /opt/minecraft/paper.jar
COPY plugins/*.jar /opt/minecraft/plugins/
COPY geyser-config.yml /opt/minecraft/geyser-config.yml
COPY --from=rcon-build /build/rcon.jar /opt/minecraft/rcon.jar
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# Stdout-only Log4j2 config — see log4j2.xml's own header for the full
# rationale. Replaces Paper's bundled default (which also writes
# logs/latest.log, unbounded, on the same PVC as the world) now that
# containerd->Alloy->Loki is the durable, retention-bounded copy.
COPY log4j2.xml /opt/minecraft/log4j2.xml
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
 && chmod 0444 /opt/minecraft/paper.jar /opt/minecraft/plugins/*.jar /opt/minecraft/rcon.jar /opt/minecraft/log4j2.xml

ENV MC_DATA_DIR=/data \
    MC_JAR=/opt/minecraft/paper.jar \
    PAPER_ARGS="--nogui" \
    HOME=/data \
    RCON_HOST=127.0.0.1 \
    RCON_PORT=25575

# Default JVM flags. The Deployment overrides JVM_OPTS; this default exists so
# `docker run` behaves the same as the pod.
#
#   -Xms2G -Xmx3G              user decision, down from 12G. See the plan.
#   -XX:MaxDirectMemorySize=512M Netty's direct-buffer pool defaults to ~Xmx when
#                              unset, which would let off-heap alone reach 3G on
#                              top of a 3G heap and blow through the 5Gi cgroup
#                              limit. Making the ceiling explicit is what keeps
#                              the 0.6 heap:limit ratio honest.
#   -XX:MaxMetaspaceSize=256M  the other unbounded native region.
#   -Dterminal.jline=false     no TTY in a pod; disable JLine and ANSI so stdout
#   -Dterminal.ansi=false      is plain, line-oriented text that Alloy and
#                              `kubectl logs` can parse without escape codes.
#   -Dlog4j.configurationFile  points at the stdout-only config above instead
#                              of Paper's bundled default, which also writes
#                              an unbounded logs/latest.log to the PVC. See
#                              log4j2.xml's header comment for the full
#                              rationale (Loki is now the durable copy).
#
# Deliberately NOT set here:
#   * Aikar's G1 flags — the bare-metal server runs on JVM defaults today.
#     Changing GC tuning and the container runtime and the heap size in one
#     move makes any regression unattributable. Adopt them later, alone, if
#     pause times warrant it. (Note -XX:+AlwaysPreTouch would also commit all
#     of Xms at startup, which interacts with the 3Gi memory *request*.)
#   * -XX:+ExitOnOutOfMemoryError — it kills the JVM instantly with no chance to
#     save, i.e. it converts an OOM into exactly the mid-write corruption this
#     whole design is built to avoid.
#   * -XX:+HeapDumpOnOutOfMemoryError — a 6G dump onto a 10Gi PVC on a 5900rpm
#     HDD turns one bad day into two.
ENV JVM_OPTS="-Xms2G -Xmx3G -XX:MaxDirectMemorySize=512M -XX:MaxMetaspaceSize=256M -Dterminal.jline=false -Dterminal.ansi=false -Dlog4j.configurationFile=/opt/minecraft/log4j2.xml"

WORKDIR /data
USER 1000:1000

# Documentation only; the Service/Deployment define the real plumbing.
EXPOSE 25566/tcp 25566/udp 25575/tcp

STOPSIGNAL SIGTERM

# Exec form. entrypoint.sh ends in `exec java ...`, so java replaces the shell
# and runs as PID 1. Do NOT add a `command:` or `args:` override in the
# Deployment that wraps this in `sh -c` — that reintroduces the signal bug.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
