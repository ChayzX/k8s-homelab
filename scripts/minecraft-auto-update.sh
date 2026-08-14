#!/usr/bin/env bash
# Keeps the Paper server updated to the latest STABLE build of the currently
# pinned Minecraft version, without reinventing anything minecraft.yaml
# already does for a safe restart.
#
# Sibling of scripts/auto-update.sh (jmusicbot's updater) -- same shell
# style, same helper functions, same error-handling philosophy. Read that
# file first if this one is confusing; it isn't repeated here.
#
# ---------------------------------------------------------------------------
# Why this script barely has to do anything clever
# ---------------------------------------------------------------------------
# minecraft.yaml's Deployment is already:
#   * strategy: Recreate -- the old pod is fully torn down (PVC released)
#     before the new one is scheduled. Never two JVMs on one world dir.
#   * preStop: `java -jar rcon.jar --graceful-stop` -- RCON `save-all flush`
#     then `stop`, polled until the RCON listener closes, all inside
#     terminationGracePeriodSeconds: 120.
# That is the entire "how do I safely restart a Minecraft server" problem,
# solved once, in the manifest. A plain `kubectl set image` on a Recreate
# Deployment walks that exact path for free. This script's only job is:
# figure out whether there's a newer STABLE build, bake it into a new image,
# and let Kubernetes do the restart it already knows how to do.
#
# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------
#   1. Poll the PaperMC v3 API for the highest-id STABLE build of $MC_VERSION.
#   2. Compare against $STATE_FILE. Nothing newer -> log and exit 0.
#   3. Download that build's jar, verify its sha256 against what the API
#      reported (belt-and-braces against a truncated/corrupted download).
#   4. `docker build` the image at $BUILD_DIR (same command the Dockerfile's
#      own header documents for a manual build).
#   5. `docker save | sudo k3s ctr images import -` to side-load it into
#      k3s's containerd (docker and k3s have entirely separate image stores;
#      see scripts/auto-update.sh's header for why this needs root).
#   6. sed-rewrite minecraft.yaml's `image:` line so git stays the record of
#      what's deployed.
#   7. `kubectl set image` (NOT `rollout restart` -- every build gets a
#      unique tag, so the tag itself is what has to change for the rollout
#      to pick up the new build; see scripts/auto-update.sh's header for why
#      `rollout restart` here would be a silent no-op that reports success).
#   8. `kubectl rollout status --timeout` -- generous, see ROLLOUT_TIMEOUT
#      below.
#   9. Post-ready sanity check: `kubectl exec ... -- java -jar rcon.jar list`.
#      Confirms the server is actually answering RCON, not just that the
#      container passed its TCP readiness probe.
#  10. Success: commit the manifest, record the new build in $STATE_FILE,
#      prune old local images/state to the last 3, notify.
#  11. Failure (rollout timeout OR RCON check fails): `rollout undo`, restore
#      the manifest from the pre-edit backup, leave $STATE_FILE untouched
#      (next cron run retries), notify.
#
# ---------------------------------------------------------------------------
# VERSION POLICY -- read this before ever touching MC_VERSION
# ---------------------------------------------------------------------------
# This script tracks PATCH/BUILD updates ONLY for the Minecraft version
# already pinned below (currently 26.2, per the Dockerfile's LABEL and
# minecraft.yaml's image tag: 26.2-b112, i.e. build 112).
# It walks 26.2 bNNN -> bNNN+1 -> bNNN+2 ... automatically, on Paper's own
# "STABLE" channel judgement, unreviewed -- same posture as jmusicbot's
# auto-update.sh has toward upstream releases.
#
# It does NOT, and must NEVER, auto-detect or auto-switch MC_VERSION to a
# newer Minecraft release (e.g. 1.21.x -> 1.22.x). That is a *major*
# behavioural change (new Java version requirements, new
# server.properties keys, plugin/data-format compatibility) that deserves a
# human reading a changelog, not a cron job. If MC_VERSION is ever bumped, it
# is a manual edit to this script by the operator -- never a decision this
# script makes on its own. The query URL is built from MC_VERSION, so
# bumping it just changes which PaperMC API endpoint gets polled; nothing
# else about the flow changes.
#
# ---------------------------------------------------------------------------
# SAFETY -- things this script must never touch
# ---------------------------------------------------------------------------
# This script only ever changes which jar gets baked into a freshly built
# image and which tag the Deployment points at. It must NEVER modify, and
# never propose changing:
#   * strategy: Recreate
#   * terminationGracePeriodSeconds: 120
#   * RCON's ClusterIP-only exposure (minecraft-rcon Service)
#   * JVM_OPTS / the container's memory requests+limits
#   * entrypoint.sh's empty-data guard
#   * the PVC (minecraft-world) or anything under /data
# If a future change needs any of those touched, that's a manifest edit by a
# human, not something bolted onto this script.
#
# ---------------------------------------------------------------------------
# OBSERVABILITY
# ---------------------------------------------------------------------------
# This script's log() output goes to stdout; the crontab line at the bottom
# redirects it to /home/chase/minecraft/logs/mc-updater.log. That directory
# is bind-mounted read-only into the standalone observability/promtail
# container (see /home/chase/docker/observability/docker-compose.yml,
# `/home/chase/minecraft/logs:/var/log/minecraft:ro`), so no new logging
# infrastructure is needed to get this script's log lines onto disk there.
#
# Honest caveat: as of this writing, promtail's `minecraft` scrape job (see
# monitoring/promtail/promtail-config.yaml) has a static `__path__` pointing
# at exactly `/var/log/minecraft/latest.log` -- a single named file, not a
# glob. mc-updater.log will land in the mounted directory and be readable
# with `kubectl`-adjacent host tools, but it will NOT reach Loki until that
# scrape config is changed to a glob (e.g. `/var/log/minecraft/*.log`) or
# gets a dedicated job of its own. That promtail config lives outside this
# repo and outside this task's scope -- flagging it here so it doesn't look
# like a broken promise later.
#
# $STATE_FILE (see below) records the last build this script deployed. A
# future Prometheus exporter change (tracked separately, out of scope here)
# could read it to expose a "minecraft build version" gauge -- not
# implemented in this script, just leaving the hook visible.
#
# ---------------------------------------------------------------------------
# Requirements on the host (all one-time, all done by hand by the operator)
# ---------------------------------------------------------------------------
# 1. sudoers: this reuses the SAME scoped grant already set up for
#    jmusicbot -- no new sudoers entry needed. See
#    /etc/sudoers.d/jmusicbot-k3s-ctr (or scripts/auto-update.sh's header if
#    it's ever missing):
#
#      chase ALL=(root) NOPASSWD: /usr/local/bin/k3s ctr images *
#
# 2. The Deployment must not try to pull the locally-built tag. Unique
#    non-`:latest` tags already default to imagePullPolicy: IfNotPresent
#    (minecraft.yaml sets this explicitly) -- this script warns loudly if it
#    ever sees Always.
#
# 3. KUBECONFIG. cron gets almost no environment; k3s is installed with
#    --write-kubeconfig-mode 644 so the default below just works.
#
# 4. The `minecraft` namespace, Deployment, and minecraft-rcon Secret must
#    already exist (see minecraft/MIGRATION.md and minecraft/secrets.md).
#    This script refuses to run if they don't -- see require_tools() below.
#
# Run manually to test: ./minecraft-auto-update.sh
# Installed via user crontab (see the bottom of this file for the line).

set -euo pipefail

# cron's PATH is typically /usr/bin:/bin -- k3s installs kubectl and k3s into
# /usr/local/bin, and docker lives in /usr/bin. Be explicit rather than
# inheriting whatever cron feels like providing.
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

# Pinned Minecraft version. See VERSION POLICY above -- do not let this
# script change this on its own.
MC_VERSION="${MC_VERSION:-26.2}"
JAVA_VERSION="${JAVA_VERSION:-25}"

# The host data dir (bare-metal leftover, kept around post-migration as home
# for logs and this script's own state -- NOT the live world any more, that
# lives on the minecraft-world PVC). Mirrors jmusicbot's DIR/STATE_FILE
# convention, just pointed at minecraft's equivalent host directory instead
# of a dedicated build tree, because minecraft's build context (the
# Dockerfile) lives in the repo, not under here.
MC_DIR="${MINECRAFT_DIR:-/home/chase/minecraft}"
STATE_FILE="$MC_DIR/.last-built-mc-version"

# The docker build context: the Dockerfile's own header documents
# `docker build -t ... .` from exactly this directory, and .dockerignore
# there already allowlists paper.jar. This script downloads the new jar
# straight into it before building, same as the documented manual flow.
BUILD_DIR="${MINECRAFT_BUILD_DIR:-/home/chase/k8s-homelab/minecraft}"

# The Kubernetes side.
MANIFEST="${MINECRAFT_MANIFEST:-$BUILD_DIR/minecraft.yaml}"
NAMESPACE="${MINECRAFT_NAMESPACE:-minecraft}"
DEPLOYMENT="${MINECRAFT_DEPLOYMENT:-minecraft}"
CONTAINER="${MINECRAFT_CONTAINER:-minecraft}"   # container name inside the pod spec

# 8-10 minutes, comfortably above the readiness probe's own allowed budget.
# minecraft.yaml's readinessProbe is initialDelaySeconds:30 +
# periodSeconds:10 * failureThreshold:30 = up to ~330s just for the probe to
# stop failing, PLUS up to terminationGracePeriodSeconds:120 for the OLD pod
# to finish its preStop RCON flush+stop before Recreate even schedules the
# new one (they are sequential, not overlapping, under Recreate). 600s
# covers both with slack left over for scheduling and HDD-bound world load.
ROLLOUT_TIMEOUT="${MINECRAFT_ROLLOUT_TIMEOUT:-600s}"

# Commit the manifest edit locally (never push) so the repo is an accurate
# record of what's running. Only ever touches $MANIFEST's path, so unrelated
# working-tree changes are left alone. Set to 0 to disable.
AUTO_COMMIT_MANIFEST="${AUTO_COMMIT_MANIFEST:-1}"

# PaperMC v3 "fill" API. Confirmed live 2026-08-11 by hand:
#   GET https://fill.papermc.io/v3/projects/paper/versions/26.2/builds
# returns a bare JSON array (newest-first, but this script does not rely on
# ordering -- see the max_by(.id) below), each element shaped like:
#   {
#     "id": 132, "time": "...", "channel": "STABLE", "commits": [...],
#     "downloads": {
#       "server:default": {
#         "name": "paper-26.2-112.jar",
#         "checksums": { "sha256": "<hex>" },
#         "size": 54846016,
#         "url": "https://fill-data.papermc.io/v1/objects/<sha256>/paper-26.2-112.jar"
#       }
#     }
#   }
PAPER_API="https://fill.papermc.io/v3/projects/paper/versions/${MC_VERSION}/builds"

WEBHOOK_URL="$(grep -oP '(?<=DISCORD_RELEASE_WEBHOOK_URL=).*' "$MC_DIR/.env" 2>/dev/null || true)"
# No $MC_DIR/.env exists yet as of this writing. Create one (mirroring
# jmusicbot's .env / .env.example) with a line:
#   DISCORD_RELEASE_WEBHOOK_URL=https://discord.com/api/webhooks/...
# to enable Discord notifications. Without it, notify() just logs locally.

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

notify() {
    local msg="$1"
    log "NOTIFY: $msg"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -sf -H "Content-Type: application/json" \
            -d "{\"content\": \"**minecraft auto-update:** ${msg}\"}" \
            "$WEBHOOK_URL" >/dev/null || log "WARN: Discord notification failed"
    fi
}

die() {
    # Fatal + notify. Used for the environment problems that are worth waking
    # someone up for, because they mean the server silently stops getting
    # updates.
    log "ERROR: $*"
    notify "🔴 $*"
    exit 1
}

# --- Preflight: the things that can be wrong before we even ask upstream ---

require_tools() {
    local missing=()
    for t in kubectl docker jq curl git sha256sum; do
        command -v "$t" >/dev/null 2>&1 || missing+=("$t")
    done
    [ ${#missing[@]} -eq 0 ] || die "missing required tools on PATH: ${missing[*]} (PATH=$PATH)"

    if ! kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" >/dev/null 2>&1; then
        die "cannot reach deployment/${DEPLOYMENT} in namespace ${NAMESPACE} (KUBECONFIG=$KUBECONFIG). Is k3s up, and has the base migration in minecraft/MIGRATION.md actually happened yet?"
    fi

    if [ ! -f "$MANIFEST" ]; then
        die "manifest not found at ${MANIFEST}; refusing to deploy an image that git has no record of."
    fi
}

# Verifies the scoped NOPASSWD sudoers entry actually works BEFORE we spend
# time on a docker build we'd then be unable to import into containerd. This
# is the SAME grant jmusicbot's auto-update.sh relies on -- no new sudoers
# entry exists or is needed for minecraft.
require_ctr_sudo() {
    K3S_BIN="$(command -v k3s || true)"
    [ -n "$K3S_BIN" ] || die "k3s binary not found on PATH (PATH=$PATH)"

    if [ "$K3S_BIN" != "/usr/local/bin/k3s" ]; then
        log "WARN: k3s resolved to ${K3S_BIN}, not /usr/local/bin/k3s -- the sudoers rule must name this exact path."
    fi

    if ! sudo -n "$K3S_BIN" ctr images ls -q >/dev/null 2>&1; then
        die "passwordless \`sudo k3s ctr images\` is not available, so the built image can't be imported into k3s containerd. This should already be granted by /etc/sudoers.d/jmusicbot-k3s-ctr (shared with jmusicbot). If that file is missing, see scripts/auto-update.sh's header for how to add it: 'chase ALL=(root) NOPASSWD: ${K3S_BIN} ctr images *' (mode 0440)."
    fi
}

# Non-fatal, but the single most likely reason a rollout mysteriously fails.
warn_on_pull_policy() {
    local policy
    policy="$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[?(@.name=='${CONTAINER}')].imagePullPolicy}" 2>/dev/null || true)"
    if [ "$policy" = "Always" ]; then
        log "WARN: container ${CONTAINER} has imagePullPolicy: Always. The locally-built tag exists only in containerd, so the kubelet will ErrImagePull. Remove it (unique tags default to IfNotPresent)."
    fi
}

# Confirms $CONTAINER actually exists in the pod spec -- `kubectl set image`
# with a wrong container name errors out, but better to say so in English.
require_container() {
    if ! kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[*].name}" 2>/dev/null | tr ' ' '\n' | grep -qx "$CONTAINER"; then
        die "deployment/${DEPLOYMENT} has no container named '${CONTAINER}'. Set MINECRAFT_CONTAINER to the right name."
    fi
}

# Commit just the manifest, locally, never push. Best-effort: a failure here
# is a bookkeeping problem, not a deployment problem.
commit_manifest() {
    local message="$1"
    [ "$AUTO_COMMIT_MANIFEST" = "1" ] || return 0
    local repo_dir
    repo_dir="$(cd "$(dirname "$MANIFEST")" && git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$repo_dir" ] || { log "WARN: ${MANIFEST} is not in a git repo; skipping commit."; return 0; }
    git -C "$repo_dir" add -- "$MANIFEST" 2>/dev/null || true
    if git -C "$repo_dir" diff --cached --quiet -- "$MANIFEST" 2>/dev/null; then
        return 0  # nothing staged, e.g. the file was already at this content
    fi
    git -C "$repo_dir" -c user.name="minecraft-auto-update.sh" -c user.email="auto-update@localhost" \
        commit -q -m "$message" -- "$MANIFEST" \
        && log "Committed ${MANIFEST}: ${message}" \
        || log "WARN: git commit of ${MANIFEST} failed; commit it by hand."
}

# Post-rollout sanity check: confirms the NEW pod is actually answering RCON,
# not just that it passed its TCP readiness probe. Runs INSIDE the pod via
# `kubectl exec`, the exact same invocation MIGRATION.md's Step 6 and
# minecraft_backup.py already use -- RCON is deliberately ClusterIP-only
# (see minecraft-rcon Service in minecraft.yaml) and this script runs on the
# host, outside the cluster network, so it cannot reach it directly. `kubectl
# exec` goes through the API server, so there's no port-forward process to
# start and reliably tear down; it's also literally the same command an
# operator would type by hand to check the same thing, so a human debugging
# a failure sees a familiar invocation in the logs.
check_rcon() {
    kubectl exec -n "$NAMESPACE" "deployment/${DEPLOYMENT}" -- \
        java -jar /opt/minecraft/rcon.jar list >/tmp/minecraft-auto-update-rcon.log 2>&1
}

# Shared failure path for both "rollout never became ready" and "rollout
# became ready but RCON isn't answering". Mirrors auto-update.sh's rollback:
# undo, re-verify, restore the pre-edit manifest, leave $STATE_FILE alone so
# the next cron run retries the same build rather than concluding it already
# shipped.
rollback_and_fail() {
    local reason="$1"
    log "Rolling back: $reason"
    kubectl rollout undo "deployment/${DEPLOYMENT}" -n "$NAMESPACE" || true
    kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT" || true
    cp "$manifest_backup" "$MANIFEST"; rm -f "$manifest_backup"
    notify "🔴 ${reason} Rolled back to \`${previous_image:-the previous ReplicaSet}\` and reverted ${MANIFEST}. State file untouched, so the next run retries. Check: \`kubectl -n ${NAMESPACE} logs deploy/${DEPLOYMENT} --previous\` and \`kubectl -n ${NAMESPACE} describe deploy/${DEPLOYMENT}\`."
    exit 1
}

# Prune locally-built images to the last 3 (rollback headroom, bounded HDD
# usage), in BOTH image stores -- docker's and containerd's -- same reason
# auto-update.sh prunes both for jmusicbot-custom.
#
# Tags are always "${MC_VERSION}-b<build>" and MC_VERSION is fixed by policy
# for the lifetime of a given set of locally-built images (see VERSION
# POLICY above), so sorting on the numeric build suffix is sufficient --
# no need to touch docker's CreatedAt, which reflects import time rather
# than actual build recency.
#
# Unlike jmusicbot's bare "jmusicbot-custom:<tag>", this image's tag already
# carries an explicit "localhost/" host component. Docker's own reference
# normalization only rewrites bare (host-less) names to docker.io/library/*;
# a name with an explicit host segment (including the literal "localhost")
# is left alone. That's *why* minecraft.yaml uses "localhost/paper-minecraft"
# instead of a bare tag -- it round-trips through `docker save` / `ctr images
# import` unchanged, so containerd lists it as exactly
# "localhost/paper-minecraft:<tag>", not "docker.io/library/...". Confirm
# with `sudo k3s ctr images ls | grep paper-minecraft` if this ever looks
# wrong.
prune_old_images() {
    local repo="localhost/paper-minecraft"
    local keep=3

    local old_docker_tags
    old_docker_tags="$(docker images "$repo" --format '{{.Tag}}' 2>/dev/null \
        | sort -t b -k2 -n -r | tail -n +$((keep + 1)) || true)"
    if [ -n "$old_docker_tags" ]; then
        echo "$old_docker_tags" | xargs -I{} -r docker rmi "${repo}:{}" >/dev/null 2>&1 || true
    fi

    local old_ctr_tags
    old_ctr_tags="$(sudo -n "$K3S_BIN" ctr images ls -q 2>/dev/null \
        | grep "^${repo}:" \
        | sed "s#^${repo}:##" \
        | sort -t b -k2 -n -r | tail -n +$((keep + 1)) || true)"
    if [ -n "$old_ctr_tags" ]; then
        echo "$old_ctr_tags" | xargs -I{} -r sudo -n "$K3S_BIN" ctr images rm "${repo}:{}" >/dev/null 2>&1 || true
    fi
}

require_tools

# --- Step 0: what's the latest STABLE build upstream? ---

build_json="$(curl -sf "$PAPER_API" | jq -c '[.[] | select(.channel == "STABLE")] | max_by(.id)')"

if [ -z "$build_json" ] || [ "$build_json" = "null" ]; then
    log "ERROR: no STABLE build found for Paper ${MC_VERSION} at ${PAPER_API} -- wrong MC_VERSION, or PaperMC hasn't published a stable build for it, aborting"
    exit 1
fi

build_id="$(echo "$build_json" | jq -r '.id // empty')"
download_url="$(echo "$build_json" | jq -r '.downloads["server:default"].url // empty')"
expected_sha256="$(echo "$build_json" | jq -r '.downloads["server:default"].checksums.sha256 // empty')"
jar_name="$(echo "$build_json" | jq -r '.downloads["server:default"].name // empty')"

if [ -z "$build_id" ] || [ -z "$download_url" ] || [ -z "$expected_sha256" ]; then
    log "ERROR: couldn't parse id/download url/sha256 out of the PaperMC API response for ${MC_VERSION}, aborting. Response was: ${build_json}"
    exit 1
fi

log "Latest STABLE build for Paper ${MC_VERSION}: b${build_id} (${jar_name})"

# --- Step 1: is there anything new to build? ---

desired_state="${MC_VERSION} ${build_id}"
current_state="$(cat "$STATE_FILE" 2>/dev/null || echo "")"

if [ "$desired_state" = "$current_state" ]; then
    log "No change since last build (${current_state}). Nothing to do."
    exit 0
fi

log "Rebuilding: Paper ${MC_VERSION} build ${build_id} (previous: ${current_state:-none})"

# Fail fast on the things that would strand a finished build.
require_ctr_sudo
require_container
warn_on_pull_policy

# --- Step 2: download, verify, build, import ---

TMP_JAR="$(mktemp)"
trap 'rm -f "$TMP_JAR"' EXIT

if ! curl -sfL -o "$TMP_JAR" "$download_url"; then
    notify "FAILED to download ${jar_name} from ${download_url}. Left the running pod untouched."
    exit 1
fi

actual_sha256="$(sha256sum "$TMP_JAR" | awk '{print $1}')"
if [ "$actual_sha256" != "$expected_sha256" ]; then
    notify "FAILED checksum verification for ${jar_name}: expected ${expected_sha256}, got ${actual_sha256}. Discarding the download; left the running pod untouched."
    exit 1
fi

log "Downloaded and verified ${jar_name} (sha256 ${actual_sha256})"

# Only overwrite the build context's jar once it's verified. It stays here
# afterward as the reference copy for the manual `docker build` documented
# in the Dockerfile's own header.
mv "$TMP_JAR" "$BUILD_DIR/paper.jar"
trap - EXIT

# Keep the cross-play plugins in the image alongside Paper. The helper pins
# exact Geyser/Floodgate builds and verifies their upstream SHA-256 checksums.
if ! ROOT_DIR="${ROOT_DIR:-/home/chase/k8s-homelab}" \
    MINECRAFT_BUILD_DIR="$BUILD_DIR" \
    bash "${ROOT_DIR:-/home/chase/k8s-homelab}/scripts/minecraft-download-plugins.sh"; then
    notify "FAILED to download or verify Geyser/Floodgate plugins. Left the running pod untouched."
    exit 1
fi

image_tag="localhost/paper-minecraft:${MC_VERSION}-b${build_id}"

if ! DOCKER_BUILDKIT=1 docker build \
    --build-arg "JAVA_VERSION=${JAVA_VERSION}" \
    --build-arg "MC_VERSION=${MC_VERSION}" \
    -t "$image_tag" "$BUILD_DIR" >/tmp/minecraft-auto-update-build.log 2>&1; then
    notify "FAILED to build ${image_tag}. Left the running pod untouched. See /tmp/minecraft-auto-update-build.log on the host."
    exit 1
fi

log "Build succeeded: ${image_tag}"

# docker and k3s keep entirely separate image stores; this is the supported
# bridge (same command Rancher documents for air-gapped installs). See
# scripts/auto-update.sh's header for the full rationale.
log "Importing ${image_tag} into k3s containerd"
if ! docker save "$image_tag" | sudo -n "$K3S_BIN" ctr images import - >/tmp/minecraft-auto-update-import.log 2>&1; then
    notify "FAILED to import \`${image_tag}\` into k3s containerd. The image exists in docker but k3s can't see it, so the pod was left untouched. See /tmp/minecraft-auto-update-import.log."
    exit 1
fi

# --- Step 3: record it in git, then deploy ---

manifest_backup="$(mktemp)"
cp "$MANIFEST" "$manifest_backup"
sed -i "s|image: localhost/paper-minecraft:.*|image: ${image_tag}|" "$MANIFEST"

if ! grep -q "image: ${image_tag}" "$MANIFEST"; then
    cp "$manifest_backup" "$MANIFEST"; rm -f "$manifest_backup"
    die "couldn't rewrite the image line in ${MANIFEST} (expected a line matching 'image: localhost/paper-minecraft:...'). Nothing was deployed."
fi

previous_image="$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
    -o jsonpath="{.spec.template.spec.containers[?(@.name=='${CONTAINER}')].image}" 2>/dev/null || true)"

# `set image`, not `rollout restart` -- see the header. The tag is unique
# per build, so this is what actually triggers a new ReplicaSet, and it's
# what exercises minecraft.yaml's Recreate + preStop safe-shutdown path.
log "Deploying ${image_tag} to deployment/${DEPLOYMENT} in ${NAMESPACE} (was: ${previous_image:-unknown})"
kubectl set image "deployment/${DEPLOYMENT}" "${CONTAINER}=${image_tag}" -n "$NAMESPACE"

if ! kubectl rollout status "deployment/${DEPLOYMENT}" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"; then
    rollback_and_fail "FAILED to roll out \`${image_tag}\` (not ready within ${ROLLOUT_TIMEOUT})."
fi

# --- Step 4: prove the new pod is actually serving, not just Ready ---

if ! check_rcon; then
    rollback_and_fail "Rolled out \`${image_tag}\` and the pod passed its readiness probe, but RCON isn't answering (see /tmp/minecraft-auto-update-rcon.log on the host) -- treating this as a failed deploy rather than trusting TCP-open alone."
fi

rm -f "$manifest_backup"
log "RCON check passed: ${image_tag} is up and serving."

# --- Step 5: success bookkeeping ---

commit_manifest "minecraft: ${image_tag}

Paper ${MC_VERSION} build ${build_id} (${jar_name}, sha256 ${expected_sha256}).
Committed automatically by scripts/minecraft-auto-update.sh."

prune_old_images

echo "$desired_state" > "$STATE_FILE"

notify "Rebuilt and redeployed: Paper ${MC_VERSION} build ${build_id} (image \`${image_tag}\`), rolled out to deployment/${DEPLOYMENT} in \`${NAMESPACE}\` and confirmed responsive over RCON."
log "Success: ${image_tag} deployed and RCON-responsive (was: ${previous_image:-unknown})."

# ---------------------------------------------------------------------------
# crontab line (user crontab, `crontab -e`):
#
#   0 3 * * * /home/chase/k8s-homelab/scripts/minecraft-auto-update.sh >> /home/chase/minecraft/logs/mc-updater.log 2>&1
#
# Deliberately not the same minute as jmusicbot's auto-update.sh (04:17) --
# no reason for two docker builds to fight over the same window on a
# 5900rpm HDD. Do NOT install this crontab line until the `minecraft`
# namespace actually exists (see minecraft/MIGRATION.md) -- require_tools()
# above will just die() loudly every run until it does, which is correct but
# noisy.
# ---------------------------------------------------------------------------
