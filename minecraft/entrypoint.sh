#!/bin/sh
#
# entrypoint.sh — start Paper with java as PID 1.
#
# ============================================================================
#  THE ONE RULE IN THIS FILE: THE LAST LINE MUST BE `exec java ...`
# ============================================================================
# Without `exec`, /bin/sh stays PID 1 and java becomes PID 2. PID 1 is the only
# process kubelet signals on pod termination, and a POSIX shell does not forward
# signals to children. The JVM would therefore never see SIGTERM, never run its
# shutdown hook, never flush chunks — and would be SIGKILLed the moment
# terminationGracePeriodSeconds expires, mid-write, on a 1.3 GB world.
# With `exec`, the shell is REPLACED by java, so java *is* PID 1 and receives
# SIGTERM directly.
#
# Verify after any change to this file:
#     kubectl exec -n minecraft deploy/minecraft -- cat /proc/1/cmdline | tr '\0' ' '
# The output must begin with the java binary path. If it begins with /bin/sh,
# STOP and fix it before letting a player join.
#
# Everything before the exec line is a fail-fast guard. It runs once, in the
# shell, before the JVM starts, and costs nothing.

set -eu

DATA_DIR="${MC_DATA_DIR:-/data}"
MC_JAR="${MC_JAR:-/opt/minecraft/paper.jar}"

die() {
    echo "=================================================================" >&2
    echo "FATAL: $*" >&2
    echo "=================================================================" >&2
    exit 1
}

cd "$DATA_DIR" || die "cannot cd to data dir $DATA_DIR"

[ -f "$MC_JAR" ] || die "$MC_JAR is missing from the image"

# ---------------------------------------------------------------------------
# Guard: refuse to start against an empty data dir.
#
# This is the single most valuable check in the file. If the PVC failed to
# mount, or the world copy was skipped, or the mount path was mistyped, Paper
# will cheerfully GENERATE A BRAND NEW WORLD, open its listener, and pass the
# readiness probe. The migration would look like a success while the real world
# sits untouched on the old disk and players spawn into an empty one.
#
# Set ALLOW_EMPTY_DATA_DIR=true only when you genuinely intend a fresh world.
# ---------------------------------------------------------------------------
if [ "${ALLOW_EMPTY_DATA_DIR:-false}" != "true" ]; then
    [ -f "$DATA_DIR/server.properties" ] \
        || die "$DATA_DIR/server.properties not found. The PVC looks empty or unmounted. \
Refusing to start, because Paper would generate a NEW world here. \
Copy the world data in (see MIGRATION.md), or set ALLOW_EMPTY_DATA_DIR=true if a fresh world is intended."
    [ -f "$DATA_DIR/world/level.dat" ] \
        || die "$DATA_DIR/world/level.dat not found. The world data is missing or incompletely copied. \
Refusing to start, because Paper would generate a NEW world here. \
See MIGRATION.md, or set ALLOW_EMPTY_DATA_DIR=true if a fresh world is intended."
fi

# Paper refuses to start without this, and generating it here would be agreeing
# to the EULA on the operator's behalf. It is copied in with the world data.
[ -f "$DATA_DIR/eula.txt" ] || die "$DATA_DIR/eula.txt not found; copy it in with the world data"

# Plugin jars are image-owned inputs, while plugin configuration and Floodgate's
# authentication key remain mutable state on the PVC. Seeding on every start
# keeps a redeploy from silently retaining an older plugin binary.
mkdir -p "$DATA_DIR/plugins"
for plugin in /opt/minecraft/plugins/*.jar; do
    [ -f "$plugin" ] || continue
    cp -f "$plugin" "$DATA_DIR/plugins/"
    echo "[entrypoint] seeded plugin: $(basename "$plugin")"
done
if [ ! -f "$DATA_DIR/plugins/Geyser-Spigot/config.yml" ]; then
    mkdir -p "$DATA_DIR/plugins/Geyser-Spigot"
    cp -f /opt/minecraft/geyser-config.yml "$DATA_DIR/plugins/Geyser-Spigot/config.yml"
    echo "[entrypoint] seeded Geyser config with Floodgate authentication"
fi

# Ownership guard. fsGroup + the manual chown in MIGRATION.md should make this a
# no-op, but a read-only or root-owned data dir produces confusing half-failures
# deep inside chunk saving rather than an obvious error at startup.
touch "$DATA_DIR/.write-test" 2>/dev/null \
    || die "$DATA_DIR is not writable by uid $(id -u):$(id -g). \
Run the chown documented in MIGRATION.md against the local-path PV directory."
rm -f "$DATA_DIR/.write-test"

echo "[entrypoint] data dir : $DATA_DIR"
echo "[entrypoint] server jar: $MC_JAR"
echo "[entrypoint] uid/gid   : $(id -u):$(id -g)"
echo "[entrypoint] jvm opts  : ${JVM_OPTS:-}"
echo "[entrypoint] exec-ing java as PID 1"

# JVM_OPTS and PAPER_ARGS are deliberately unquoted: they are flag lists and
# must undergo word splitting. Neither contains user-supplied data.
# shellcheck disable=SC2086
exec java ${JVM_OPTS:-} -jar "$MC_JAR" ${PAPER_ARGS:---nogui}
